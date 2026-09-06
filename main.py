import os
import glob
import argparse
from datetime import datetime, timedelta

from src.config import INPUT_DIR
from src.llm import process_statement_pdf
from src.sheets import export_statements_to_sheet
from src.plaid_client import create_link_token, exchange_public_token, get_transactions, load_tokens
from src.schemas import PlaidTransaction, PlaidAccount
from src.plaid_transformer import transform_plaid_transactions

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
        help="Fetch transactions from Plaid for the last 30 days."
    )
    args = parser.parse_args()

    if args.plaid_link:
        link_token = create_link_token()
        print("\n--- Plaid Link Setup ---")
        print(f"1. Open the following URL in your browser to link your account:")
        print(f"   https://sandbox.plaid.com/link/v2/stable/link.html?isWebview=true&token={link_token}")
        public_token = input("\n2. After linking, paste the generated public_token here and press Enter: ")
        
        if public_token:
            access_token, item_id = exchange_public_token(public_token.strip())
            print(f"\n✓ Successfully exchanged public token for an access token.")
            print(f"   - Item ID: {item_id}")
            print(f"   - Access Token: {access_token}")
            print("   (This access token has been saved securely for future use.)")
        return

    if args.fetch_transactions:
        print("\nFetching transactions from Plaid...")
        tokens = load_tokens()
        if not tokens:
            print("No Plaid access tokens found. Please run with --plaid-link to connect an account first.")
            return
            
        summaries = []
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=30)
        
        for item_id, access_token in tokens.items():
            print(f"\nFetching transactions for Item ID: {item_id}")
            response = get_transactions(access_token, start_date.isoformat(), end_date.isoformat())
            
            accounts = [PlaidAccount(**acc) for acc in response['accounts']]
            transactions = [PlaidTransaction(**tx) for tx in response['transactions']]

            for account in accounts:
                account_transactions = [tx for tx in transactions if tx.account_id == account.account_id]
                if account_transactions:
                    summary = transform_plaid_transactions(account_transactions, account, start_date.isoformat(), end_date.isoformat())
                    summaries.append(summary)
                    print(f"   ✓ {summary.card_issuer} ({summary.account_name}): {len(summary.transactions)} transactions")

        if summaries:
            print("\nExporting aggregated data to Google Sheets...")
            export_statements_to_sheet(summaries)
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
            print(f"   ✓ {summary.card_issuer} ({summary.account_name}): {len(summary.transactions)} transactions")
        except Exception as e:
            print(f"   ✗ Failed to process {filename}: {e}")

    if summaries:
        print("\nExporting aggregated data to Google Sheets...")
        export_statements_to_sheet(summaries)
        print("\nBatch process complete!")

if __name__ == "__main__":
    main()
