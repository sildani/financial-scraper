import os
import sys
import glob
import argparse
from datetime import date
import time
import webbrowser

from src.config import INPUT_DIR, current_plaid_environment
from src.llm import process_statement_pdf
from src.sheets import export_statements_to_sheet
from src.plaid_client import (
    create_link_token, exchange_public_token, fetch_links, load_tokens, get_public_token_from_link_session,
    links_needing_relink, link_session_finished, link_error_code,
)
from src.plaid_transformer import summarize_link
from src.linked_movements import pair_linked_movements
from src.export_window import export_window, history_warning

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

    print(f"\n✗ Plaid Link flow timed out after {int(TIMEOUT_S / 60)} minutes. Please try again.")
    return None

def main():
    parser = argparse.ArgumentParser(description="Financial Statement Scraper & Analyzer")
    parser.add_argument(
        "-f", "--force", 
        action="store_true", 
        help="Force re-processing of PDFs via Gemini API, bypassing local cache"
    )
    parser.add_argument(
        "--plaid-link",
        action="store_true",
        help="Start the Plaid Link flow to connect a new financial institution."
    )
    parser.add_argument(
        "--relink",
        action="store_true",
        help="Re-link every Link that needs it, in place, via Plaid Link update mode."
    )
    parser.add_argument(
        "--fetch-transactions",
        action="store_true",
        help="Fetch transactions from Plaid for the Export window (default: the last 30 days)."
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
    args = parser.parse_args()

    # A Statement's own period defines what it covers, so the Export window is Plaid mode only
    if (args.since or args.until) and not args.fetch_transactions:
        sys.exit("Error: --since and --until only apply with --fetch-transactions")

    # Fail before any work: every run reads or writes Environment-specific Links and Sheets
    try:
        environment = current_plaid_environment()
    except ValueError as e:
        sys.exit(f"Error: {e}")
    print(f"Environment: {environment.name}")

    if args.plaid_link:
        link_token, hosted_link_url = create_link_token()
        print("\n--- Plaid Link Setup ---")
        public_token = run_hosted_link(hosted_link_url, lambda: get_public_token_from_link_session(link_token))
        if not public_token:
            return

        access_token, item_id = exchange_public_token(public_token.strip())
        print(f"\n✓ Successfully exchanged public token for an access token.")
        print(f"   - Item ID: {item_id}")
        print("   (This access token has been saved securely for future use.)")
        return

    if args.relink:
        needing = links_needing_relink()
        if not needing:
            print("\nEvery Link is Active; nothing to re-link.")
            return
        tokens = load_tokens()
        for link in needing:
            # Update mode repairs the Link in place, so its access token and the token file stay as they are
            link_token, hosted_link_url = create_link_token(access_token=tokens[link.item_id])
            print(f"\n--- Re-link {link.institution} ---")
            if not run_hosted_link(hosted_link_url, lambda: link_session_finished(link_token)):
                return
            if link_error_code(tokens[link.item_id]) == "ITEM_LOGIN_REQUIRED":
                print(f"✗ {link.institution} still needs re-linking. Run --relink again.")
            else:
                print(f"✓ {link.institution} is re-linked.")
        return

    if args.fetch_transactions:
        print("\nFetching transactions from Plaid...")
        tokens = load_tokens()
        if not tokens:
            print("No Plaid access tokens found. Please run with --plaid-link to connect an account first.")
            return
            
        try:
            window = export_window(args.since, args.until, date.today())
        except ValueError as e:
            sys.exit(f"Error: {e}")
        print(f"Export window: {window.start} to {window.end}")
        warning = history_warning(window, date.today())
        if warning:
            print(f"   ! {warning}")

        fetched = fetch_links(tokens, window.start, window.end)
        responses = fetched.responses
        for link in fetched.failed:
            reason = "needs re-linking (run --relink)" if link.needs_relink else f"failed with {link.error_code}; try again later"
            print(f"   ✗ {link.institution} {reason}")
        if not responses:
            sys.exit("Error: no Link could be fetched, so no Export was written")

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

        if summaries:
            print("\nExporting aggregated data to Google Sheets...")
            missing = [link.institution for link in fetched.failed]
            export_statements_to_sheet(summaries, movements, missing=missing)
            if missing:
                print(f"\n! Partial Export: missing {', '.join(missing)}")
            print("\nBatch process complete!")
        return

    # Default PDF processing logic
    pdf_files = glob.glob(os.path.join(INPUT_DIR, "*.pdf")) + glob.glob(os.path.join(INPUT_DIR, "*.PDF"))
    pdf_files = sorted(list(set(pdf_files)))

    if not pdf_files:
        print(f"No PDF statements found in '{INPUT_DIR}/'. Add your files and try again.")
        return

    print(f"Found {len(pdf_files)} PDF statement(s) in '{INPUT_DIR}/'\n")

    summaries = []
    for index, pdf_path in enumerate(pdf_files, start=1):
        filename = os.path.basename(pdf_path)
        print(f"[{index}/{len(pdf_files)}] Processing: {filename}")
        
        try:
            summary = process_statement_pdf(pdf_path, force_reprocess=args.force)
            summaries.append(summary)
            print(f"   ✓ {summary.institution} ({summary.account_name}): {len(summary.transactions)} transactions")
        except Exception as e:
            print(f"   ✗ Failed to process {filename}: {e}")

    if summaries:
        print("\nExporting aggregated data to Google Sheets...")
        export_statements_to_sheet(summaries)
        print("\nBatch process complete!")

if __name__ == "__main__":
    main()
