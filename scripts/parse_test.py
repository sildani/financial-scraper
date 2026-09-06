import json
from agent import process_statement_pdf

PDF_PATH = "input/apple-card-2026-08.pdf"

def main():
    print(f"Processing: {PDF_PATH} ...")
    summary = process_statement_pdf(PDF_PATH)
    
    print("\n" + "="*50)
    print("STATEMENT SUMMARY")
    print("="*50)
    print(f"Issuer:            {summary.card_issuer}")
    print(f"Statement Period:  {summary.statement_period}")
    print(f"Total Balance:     ${summary.total_balance_due:,.2f}")
    print(f"Payment Due Date:  {summary.payment_due_date}")
    print(f"Interest Charged:  ${summary.interest_charged:,.2f}")
    print(f"Total Transactions Parsed: {len(summary.transactions)}")
    
    print("\n" + "="*50)
    print("SAMPLE TRANSACTIONS (First 5)")
    print("="*50)
    for tx in summary.transactions[:5]:
        print(f"{tx.transaction_date} | {tx.clean_merchant:<25} | ${tx.amount:>7.2f} | {tx.category.value}")

if __name__ == "__main__":
    main()