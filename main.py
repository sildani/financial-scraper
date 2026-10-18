import os
import sys
import glob
import argparse
from datetime import date
import time
import webbrowser
from typing import List
import plaid
from pydantic import BaseModel

from src.config import INPUT_DIR, current_plaid_environment
from src.llm import process_statement_pdf
from src.sheets import export_statements_to_sheet
from src.plaid_client import (
    create_link_token, exchange_public_token, fetch_links, load_tokens, get_public_token_from_link_session,
    links_needing_relink, link_session_finished, link_error_code, error_needs_relink, FailedLink, FetchedLinks, RelinkCheck,
    error_code as plaid_error_code,
)
from src.plaid_transformer import summarize_link
from src.linked_movements import pair_linked_movements, LinkedMovements
from src.export_window import export_window, history_warning, ExportWindow
from src.schemas import AccountSummary

def run_hosted_link(hosted_link_url, poll):
    """Opens hosted Link in the browser and polls until poll() returns something truthy; None on timeout."""
    print(f"1. Your browser will now open to complete the Plaid Link flow.")
    print(f"   If it doesn't, please open this URL manually:")
    print(f"   {hosted_link_url}")

    webbrowser.open(hosted_link_url)

    POLLING_INTERVAL_S = 2
    TIMEOUT_S = 300  # 5 minutes
    start_time = time.time()

    print(f"\n2. Waiting for you to complete the flow in your browser... (this will time out in {int(TIMEOUT_S / 60)} minutes)")

    while time.time() - start_time < TIMEOUT_S:
        result = poll()
        if result:
            print("\n✓ Plaid Link flow completed successfully!")
            return result
        time.sleep(POLLING_INTERVAL_S)

    print(f"\n✗ Plaid Link flow timed out after {int(TIMEOUT_S / 60)} minutes.")
    return None

class PlaidExport(BaseModel):
    fetched: FetchedLinks
    movements: LinkedMovements | None = None  # None when no Link could be fetched
    summaries: List[AccountSummary] = []
    tabs: List[str] = []  # Empty when no Export was written

def run_plaid_export(tokens: dict, window: ExportWindow, spreadsheet=None) -> PlaidExport:
    """A Plaid mode Export of every Link in tokens over window, written to the Environment's Sheet."""
    fetched = fetch_links(tokens, window.start, window.end)
    responses = fetched.responses
    for link in fetched.failed:
        reason = "needs re-linking (run --relink)" if link.needs_relink else f"failed with {link.error_code}; try again later"
        print(f"   ✗ {link.institution} {reason}")
    if not responses:
        return PlaidExport(fetched=fetched)

    # Card Payments and Internal Transfers cross Links, so pair over every Link at once
    movements = pair_linked_movements(responses)
    print(f"\nLinked Movements: {len(movements.paired)} paired, {len(movements.unpaired)} unpaired")
    for side in movements.unpaired:
        print(f"   ! Unpaired: {side.institution} ({side.account_name}) {side.date} {side.description} {side.amount:,.2f}")

    # Spend depends on Linked Movements, so summarize only once they're paired
    summaries = []
    for response in responses:
        for summary in summarize_link(response, window.start.isoformat(), window.end.isoformat(), movements):
            summaries.append(summary)
            print(f"   ✓ {summary.institution} ({summary.account_name}): {len(summary.transactions)} transactions")

    tabs = []
    if summaries:
        print("\nExporting aggregated data to Google Sheets...")
        missing = [link.institution for link in fetched.failed]
        tabs = export_statements_to_sheet(summaries, movements, missing=missing, spreadsheet=spreadsheet)
        if missing:
            print(f"\n! Partial Export: missing {', '.join(missing)}")
        print("\nBatch process complete!")
    return PlaidExport(fetched=fetched, movements=movements, summaries=summaries, tabs=tabs)

def hosted_relink_session(access_token: str) -> bool:
    """Runs one Link's update mode session in hosted Link; False if it timed out."""
    # Update mode repairs the Link in place, so its access token and the token file stay as they are
    link_token, hosted_link_url = create_link_token(access_token=access_token)
    return bool(run_hosted_link(hosted_link_url, lambda: link_session_finished(link_token)))

class RelinkRun(BaseModel):
    relinked: List[FailedLink] = []
    needs_relink: List[FailedLink] = []
    unchecked: List[FailedLink] = []  # Never offered: whether they need re-linking is unknown

    @property
    def complete(self) -> bool:
        return not self.needs_relink and not self.unchecked

def run_relink(check: RelinkCheck, tokens: dict, relink_session=hosted_relink_session, error_code=link_error_code) -> RelinkRun:
    """Offers each Link in check.needing for re-linking in turn, then lists how each one ended."""
    run = RelinkRun(unchecked=check.unchecked)
    print_unchecked(run.unchecked)
    for link in check.needing:
        print(f"\n--- Re-link {link.institution} ---")
        access_token = tokens[link.item_id]
        try:
            # A timed-out session still needs re-linking, but the other Links are still offered
            if not relink_session(access_token):
                run.needs_relink.append(link)
                continue
            code = error_code(access_token)
        except plaid.ApiException as e:
            run.needs_relink.append(link.model_copy(update={"error_code": plaid_error_code(e)}))
            continue
        if error_needs_relink(code):
            run.needs_relink.append(link)
        else:
            # Re-linking can't fix another error, such as INSTITUTION_DOWN, so the login counts as repaired
            run.relinked.append(link.model_copy(update={"error_code": code}) if code else link)

    print("\nRe-link summary:")
    print_unchecked(run.unchecked)
    ended = {link.item_id: relinked_line(link) for link in run.relinked}
    ended |= {link.item_id: needs_relink_line(link) for link in run.needs_relink}
    for link in check.needing:
        print(ended[link.item_id])
    if not run.complete:
        print("Run --relink again to retry the Links that still need it.")
    return run

def other_error(link: FailedLink) -> str | None:
    """The Link's Plaid error code, unless it's Needs re-link, which every offered Link started as."""
    return None if error_needs_relink(link.error_code) else link.error_code

def relinked_line(link: FailedLink) -> str:
    code = other_error(link)
    return f"   ✓ {link.institution} is re-linked" + (f" (Plaid reports {code})" if code else "")

def needs_relink_line(link: FailedLink) -> str:
    code = other_error(link)
    return f"   ✗ {link.institution} still needs re-linking" + (f" ({code})" if code else "")

def print_unchecked(links: List[FailedLink]):
    for link in links:
        print(f"   ✗ {link.institution} couldn't be checked ({link.error_code})")

class StatementExport(BaseModel):
    summaries: List[AccountSummary] = []
    failed: List[str] = []  # PDF file names of the Statements that couldn't be processed
    tabs: List[str] = []  # Empty when no Export was written

def run_statement_export(pdf_paths: List[str], force: bool = False, parse=process_statement_pdf, spreadsheet=None) -> StatementExport:
    """A Statement mode Export of the Statement PDFs at pdf_paths, written to the Environment's Sheet."""
    summaries, failed = [], []
    for index, pdf_path in enumerate(pdf_paths, start=1):
        filename = os.path.basename(pdf_path)
        print(f"[{index}/{len(pdf_paths)}] Processing: {filename}")

        try:
            summary = parse(pdf_path, force_reprocess=force)
            summaries.append(summary)
            print(f"   ✓ {summary.institution} ({summary.account_name}): {len(summary.transactions)} transactions")
        except Exception as e:
            failed.append(filename)
            print(f"   ✗ Failed to process {filename}: {e}")
    if not summaries:
        return StatementExport(failed=failed)

    print("\nExporting aggregated data to Google Sheets...")
    tabs = export_statements_to_sheet(summaries, missing=failed, spreadsheet=spreadsheet)
    print("\nBatch process complete!")
    if failed:
        print(f"\n! Partial Export: missing {', '.join(failed)}")
        print("   Run --statements again without -f to retry only the failed Statements; the rest are cached.")
    return StatementExport(summaries=summaries, failed=failed, tabs=tabs)

def parse_args(argv=None) -> argparse.Namespace:
    """
    Parses the command line into args whose action says what the run does: "plaid-export" (the
    default, a Plaid mode Export), "statement-export" (a Statement mode Export), "link" or "relink".
    """
    parser = argparse.ArgumentParser(description="Financial Statement Scraper & Analyzer")
    # Without one of these, the run is a Plaid mode Export
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--statements",
        action="store_true",
        help=f"Statement mode: export from the Statement PDFs in {INPUT_DIR}/ instead of Plaid."
    )
    action.add_argument(
        "--plaid-link",
        action="store_true",
        help="Start the Plaid Link flow to connect a new financial institution."
    )
    action.add_argument(
        "--relink",
        action="store_true",
        help="Re-link every Link that needs it, in place, via Plaid Link update mode."
    )
    parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="Force re-processing of PDFs via Gemini API, bypassing local cache"
    )
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="First day of the Export window (default: 30 days before --until). Plaid mode only."
    )
    parser.add_argument(
        "--until",
        metavar="YYYY-MM-DD",
        help="Last day of the Export window (default: today). Plaid mode only."
    )
    args = parser.parse_args(argv)

    if args.statements:
        args.action = "statement-export"
    elif args.plaid_link:
        args.action = "link"
    elif args.relink:
        args.action = "relink"
    else:
        args.action = "plaid-export"

    # A Statement's own period defines what it covers, so the Export window is Plaid mode only
    if (args.since or args.until) and args.action != "plaid-export":
        parser.error("--since and --until only apply to a Plaid mode Export")
    # The cache it bypasses only holds parsed Statements
    if args.force and args.action != "statement-export":
        parser.error("-f/--force only applies with --statements")
    return args

def main():
    args = parse_args()

    # Fail before any work: every run reads or writes Environment-specific Links and Sheets
    try:
        environment = current_plaid_environment()
    except ValueError as e:
        sys.exit(f"Error: {e}")
    print(f"Environment: {environment.name}")

    if args.action == "link":
        link_token, hosted_link_url = create_link_token()
        print("\n--- Plaid Link Setup ---")
        public_token = run_hosted_link(hosted_link_url, lambda: get_public_token_from_link_session(link_token))
        if not public_token:
            print("Please try again.")
            return

        access_token, item_id = exchange_public_token(public_token.strip())
        print(f"\n✓ Successfully exchanged public token for an access token.")
        print(f"   - Item ID: {item_id}")
        print("   (This access token has been saved securely for future use.)")
        return

    if args.action == "relink":
        check = links_needing_relink()
        if not check.needing and not check.unchecked:
            print("\nEvery Link is Active; nothing to re-link.")
            return
        if not run_relink(check, load_tokens()).complete:
            sys.exit("Error: some Links still need re-linking")
        return

    if args.action == "plaid-export":
        print("\nFetching transactions from Plaid...")
        tokens = load_tokens()
        if not tokens:
            sys.exit(
                f"Error: no Links in the {environment.name} Environment. Run --plaid-link to add one, "
                f"or --statements to export from the Statement PDFs in {INPUT_DIR}/."
            )

        try:
            window = export_window(args.since, args.until, date.today())
        except ValueError as e:
            sys.exit(f"Error: {e}")
        print(f"Export window: {window.start} to {window.end}")
        warning = history_warning(window, date.today())
        if warning:
            print(f"   ! {warning}")

        if not run_plaid_export(tokens, window).fetched.responses:
            sys.exit("Error: no Link could be fetched, so no Export was written")
        return

    # Statement mode: every other action has returned
    pdf_files = glob.glob(os.path.join(INPUT_DIR, "*.pdf")) + glob.glob(os.path.join(INPUT_DIR, "*.PDF"))
    pdf_files = sorted(list(set(pdf_files)))

    if not pdf_files:
        print(f"No PDF statements found in '{INPUT_DIR}/'. Add your files and try again.")
        return

    print(f"Found {len(pdf_files)} PDF statement(s) in '{INPUT_DIR}/'\n")

    if not run_statement_export(pdf_files, args.force).summaries:
        sys.exit("Error: no Statement could be processed, so no Export was written")

if __name__ == "__main__":
    main()
