import argparse
import sys
import time
import webbrowser
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Callable, List

import plaid
from pydantic import BaseModel

from src.adapters import adapters as all_adapters
from src.assisted_downloads import Assist, prune_archive
from src.config import (
    DOWNLOAD_ACCOUNTS_FILE,
    DOWNLOAD_ARCHIVE_DIR,
    INPUT_DIR,
    current_plaid_environment,
)
from src.download_accounts import DownloadAccount, load_download_accounts
from src.downloads import Adapter, fetch_downloads
from src.export_window import ExportWindow, export_window, history_warning
from src.linked_movements import LinkedMovements, pair_linked_movements
from src.plaid_client import (
    FailedLink,
    FetchedLinks,
    LinksClient,
    RelinkCheck,
    create_link_token,
    error_code as plaid_error_code,
    error_needs_relink,
    exchange_public_token,
    fetch_links,
    get_plaid_client,
    get_public_token_from_link_session,
    institution_name,
    link_error_code,
    link_session_finished,
    links_needing_relink,
    load_tokens,
)
from src.plaid_transformer import summarize_link
from src.schemas import AccountSummary, link_label
from src.sheets import Spreadsheet, export_to_sheet


def run_hosted_link[T](hosted_link_url: str, poll: Callable[[], T | None]):
    """Opens hosted Link in the browser and polls until poll() returns something truthy; None on timeout."""
    print("1. Your browser will now open to complete the Plaid Link flow.")
    print("   If it doesn't, please open this URL manually:")
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

class Export(BaseModel):
    fetched: FetchedLinks
    movements: LinkedMovements | None = None  # None when no Link could be fetched
    summaries: List[AccountSummary] = []
    missing: List[str] = []  # Each failed Link, then each missing Download Account, as output names them
    tabs: List[str] = []  # Empty when no Export was written

def run_export(tokens: dict, download_accounts: List[DownloadAccount], window: ExportWindow, input_files: List[Path], adapters: dict[str, Adapter] | None = None, spreadsheet: Spreadsheet | None = None, client: LinksClient | None = None, assist: Assist | None = None) -> Export:
    """
    One Export over window of every Account on every Link in tokens, then every Download Account
    from its file among input_files or, failing that, through assist, written to the Environment's Sheet.
    """
    if assist:
        prune_archive(assist)
    # With no Links there's nothing to ask Plaid, and no Plaid client is needed
    fetched = fetch_links(tokens, window.start, window.end, client=client) if tokens else FetchedLinks(responses=[], failed=[])
    responses = fetched.responses
    for link in fetched.failed:
        reason = "needs re-linking (run --relink)" if link.needs_relink else f"failed with {link.error_code}; try again later"
        print(f"   ✗ {link.label} {reason}")

    summaries, movements = [], None
    if responses:
        # Card Payments and Internal Transfers cross Links, so pair over every Link at once
        movements = pair_linked_movements(responses)
        print(f"\nLinked Movements: {len(movements.paired)} paired, {len(movements.unpaired)} unpaired")
        for side in movements.unpaired:
            print(f"   ! Unpaired: {side.institution} ({side.account_name}) {side.date} {side.description} {side.amount:,.2f}")

        # Spend depends on Linked Movements, so summarize only once they're paired
        for response in responses:
            for summary in summarize_link(response, window, movements):
                summaries.append(summary)
                print(f"   ✓ {summary.institution} ({summary.account_name}): {len(summary.transactions)} transactions")

    if download_accounts:
        print(f"\nFetching Downloads (a matching file in {INPUT_DIR.name}/ is used first)...")
    downloads = fetch_downloads(download_accounts, input_files, window, adapters or all_adapters(), assist=assist)
    summaries += [download.summary for download in downloads.downloads]

    missing = [link.label for link in fetched.failed] + [account.label for account in downloads.missing]
    tabs = []
    if summaries:
        print("\nExporting aggregated data to Google Sheets...")
        tabs = export_to_sheet(summaries, movements, missing=missing, spreadsheet=spreadsheet)
        if missing:
            print(f"\n! Partial Export: missing {', '.join(missing)}")
        print("\nBatch process complete!")
    return Export(fetched=fetched, movements=movements, summaries=summaries, missing=missing, tabs=tabs)

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

def run_relink(check: RelinkCheck, tokens: dict, relink_session: Callable[[str], bool] = hosted_relink_session, error_code: Callable[[str], str | None] = link_error_code) -> RelinkRun:
    """Offers each Link in check.needing for re-linking in turn, then lists how each one ended."""
    run = RelinkRun(unchecked=check.unchecked)
    print_unchecked(run.unchecked)
    for link in check.needing:
        print(f"\n--- Re-link {link.label} ---")
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
    return f"   ✓ {link.label} is re-linked" + (f" (Plaid reports {code})" if code else "")

def needs_relink_line(link: FailedLink) -> str:
    code = other_error(link)
    return f"   ✗ {link.label} still needs re-linking" + (f" ({code})" if code else "")

def print_unchecked(links: List[FailedLink]):
    for link in links:
        print(f"   ✗ {link.label} couldn't be checked ({link.error_code})")

class Action(str, Enum):
    """What a run does."""
    EXPORT = "export"  # The default: an Export of every Plaid Account and Download Account
    LINK = "link"
    RELINK = "relink"

def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    """Parses the command line into args whose action is the Action the run does."""
    parser = argparse.ArgumentParser(
        description=(
            "Exports every Account to Google Sheets in one run: each Account on every Plaid Link, then each "
            f"Download Account in {DOWNLOAD_ACCOUNTS_FILE.name}, from its file in {INPUT_DIR.name}/ or else by opening "
            "its page and waiting for the file in your downloads folder (Production only)."
        )
    )
    # Without one of these, the run is an Export
    action = parser.add_mutually_exclusive_group()
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
        help="Re-parse Statement PDFs with Gemini, bypassing the local cache."
    )
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="First day of the Export window (default: 30 days before --until)."
    )
    parser.add_argument(
        "--until",
        metavar="YYYY-MM-DD",
        help="Last day of the Export window (default: today)."
    )
    args = parser.parse_args(argv)

    if args.plaid_link:
        args.action = Action.LINK
    elif args.relink:
        args.action = Action.RELINK
    else:
        args.action = Action.EXPORT

    if (args.since or args.until) and args.action != Action.EXPORT:
        parser.error("--since and --until only apply to an Export")
    # The cache it bypasses only holds parsed Statements, which only an Export reads
    if args.force and args.action != Action.EXPORT:
        parser.error("-f/--force only applies to an Export")
    return args

def main():
    args = parse_args()

    # Fail before any work: every run reads or writes Environment-specific Links and Sheets
    try:
        environment = current_plaid_environment()
    except ValueError as e:
        sys.exit(f"Error: {e}")
    print(f"Environment: {environment.name}")

    if args.action == Action.LINK:
        link_token, hosted_link_url = create_link_token()
        print("\n--- Plaid Link Setup ---")
        public_token = run_hosted_link(hosted_link_url, lambda: get_public_token_from_link_session(link_token))
        if not public_token:
            print("Please try again.")
            return

        client = get_plaid_client()
        access_token, item_id = exchange_public_token(public_token.strip(), client=client)
        print("\n✓ Successfully exchanged public token for an access token.")
        institution = institution_name(client, item_id, access_token)
        print(f"   - Link: {link_label(item_id, institution)}")
        print(f"   - Item ID: {item_id}")
        print("   (This access token has been saved securely for future use.)")
        return

    if args.action == Action.RELINK:
        check = links_needing_relink()
        if not check.needing and not check.unchecked:
            print("\nEvery Link is Active; nothing to re-link.")
            return
        if not run_relink(check, load_tokens()).complete:
            sys.exit("Error: some Links still need re-linking")
        return

    # An Export: every other action has returned
    adapters = all_adapters(force=args.force)
    try:
        download_config = load_download_accounts(DOWNLOAD_ACCOUNTS_FILE, environment.name, adapters)
    except ValueError as e:
        sys.exit(f"Error: {e}")
    download_accounts = download_config.accounts
    assist = Assist(downloads_dir=download_config.downloads_dir, archive_dir=DOWNLOAD_ARCHIVE_DIR)
    tokens = load_tokens()
    if not tokens and not download_accounts:
        sys.exit(
            f"Error: no Links in the {environment.name} Environment and no Download Accounts. Run --plaid-link "
            f"to add a Link, or, in Production, list Download Accounts in {DOWNLOAD_ACCOUNTS_FILE.name}."
        )

    try:
        window = export_window(args.since, args.until, date.today())
    except ValueError as e:
        sys.exit(f"Error: {e}")
    print(f"Export window: {window.start} to {window.end}")
    warning = history_warning(window, date.today())
    if tokens and warning:
        print(f"   ! {warning}")

    if tokens:
        print("\nFetching transactions from Plaid...")
    input_files = [path for path in INPUT_DIR.glob("*") if path.is_file() and not path.name.startswith(".")]
    if not run_export(tokens, download_accounts, window, input_files, adapters=adapters, assist=assist).summaries:
        sys.exit("Error: no Link or Download Account could be fetched, so no Export was written")

if __name__ == "__main__":
    main()
