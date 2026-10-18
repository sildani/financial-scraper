import json
from agent import process_statement_pdf

PDF_PATH = "input/apple-card-2026-08.pdf"

def main():
    print(f"Processing: {PDF_PATH} ...")
    summary = process_statement_pdf(PDF_PATH)
    
    print("\n" + "="*50)
    print("STATEMENT SUMMARY")
    print("="*50)
    print(f"Institution:       {summary.institution}")
    print(f"Statement Period:  {summary.period}")
    print(f"Balance:           ${summary.balance:,.2f}")
    print(f"Amount Due:        {summary.amount_due}")
    print(f"Due Date:          {summary.due_date}")
    print(f"Interest Charged:  ${summary.interest_charged:,.2f}")
    print(f"Total Transactions Parsed: {len(summary.transactions)}")
    
    print("\n" + "="*50)
    print("SAMPLE TRANSACTIONS (First 5)")
    print("="*50)
    for tx in summary.transactions[:5]:
        print(f"{tx.transaction_date} | {tx.clean_merchant:<25} | ${tx.amount:>7.2f} | {tx.category.value}")

if __name__ == "__main__":
    main()