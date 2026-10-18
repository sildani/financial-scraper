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
from src.plaid_client import create_link_token, exchange_public_token, get_transactions, load_tokens, get_public_token_from_link_session
from src.plaid_transformer import summarize_link
from src.linked_movements import pair_linked_movements
from src.export_window import export_window, history_warning

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
        print(f"1. Your browser will now open to complete the Plaid Link flow.")
        print(f"   If it doesn't, please open this URL manually:")
        print(f"   {hosted_link_url}")

        webbrowser.open(hosted_link_url)

        public_token = None
        POLLING_INTERVAL_S = 2
        TIMEOUT_S = 300  # 5 minutes
        start_time = time.time()

        print(f"\n2. Waiting for you to complete the flow in your browser... (this will time out in {int(TIMEOUT_S / 60)} minutes)")

        while time.time() - start_time < TIMEOUT_S:
            public_token = get_public_token_from_link_session(link_token)
            if public_token:
                print("\n✓ Plaid Link flow completed successfully!")
                break
            time.sleep(POLLING_INTERVAL_S)
        
        if not public_token:
            print(f"\n✗ Plaid Link flow timed out after {int(TIMEOUT_S / 60)} minutes. Please try again.")
            return

        access_token, item_id = exchange_public_token(public_token.strip())
        print(f"\n✓ Successfully exchanged public token for an access token.")
        print(f"   - Item ID: {item_id}")
        print("   (This access token has been saved securely for future use.)")
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

        responses = []
        for item_id, access_token in tokens.items():
            print(f"\nFetching transactions for Item ID: {item_id}")
            responses.append(get_transactions(access_token, window.start, window.end))

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
            export_statements_to_sheet(summaries, movements)
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
