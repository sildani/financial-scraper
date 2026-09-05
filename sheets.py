import os
from datetime import datetime, timezone
from collections import defaultdict
import gspread
from dotenv import load_dotenv
from schemas import StatementSummary

load_dotenv()

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_FILE = "service_account.json"


def get_gspread_client() -> gspread.Client:
    return gspread.service_account(filename=SERVICE_ACCOUNT_FILE)


def export_statements_to_sheet(summaries: list[StatementSummary]):
    """Exports structured data to three timestamped tabs:
    - Summary_YYYYMMDDHHMMSS
    - Details_YYYYMMDDHHMMSS
    - Analysis_YYYYMMDDHHMMSS
    """
    gc = get_gspread_client()
    sh = gc.open_by_key(SPREADSHEET_ID)

    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    
    summary_tab = f"Summary_{timestamp_str}"
    details_tab = f"Details_{timestamp_str}"
    analysis_tab = f"Analysis_{timestamp_str}"

    # 1. Summary Sheet
    summary_ws = sh.add_worksheet(title=summary_tab, rows=100, cols=10)
    summary_ws.append_row([
        "Issuer", "Account Name", "Statement Period", 
        "Total Balance Due", "Payment Due Date", "Interest Charged", "Transaction Count"
    ])
    summary_rows = [
        [s.card_issuer, s.account_name, s.statement_period, s.total_balance_due, s.payment_due_date, s.interest_charged, len(s.transactions)]
        for s in summaries
    ]
    summary_ws.append_rows(summary_rows)

    # 2. Detailed Spending Sheet
    details_ws = sh.add_worksheet(title=details_tab, rows=2000, cols=10)
    details_ws.append_row([
        "Issuer", "Account Name", "Date", "Raw Description", "Clean Merchant", "Amount", "Category"
    ])
    details_rows = []
    for s in summaries:
        for tx in s.transactions:
            details_rows.append([
                s.card_issuer, s.account_name, tx.transaction_date, tx.description, tx.clean_merchant, tx.amount, tx.category.value
            ])
    details_ws.append_rows(details_rows)

    # 3. Aggregated Analysis Sheet
    analysis_ws = sh.add_worksheet(title=analysis_tab, rows=500, cols=10)
    
    category_totals = defaultdict(float)
    merchant_totals = defaultdict(float)

    for s in summaries:
        for tx in s.transactions:
            category_totals[tx.category.value] += tx.amount
            merchant_totals[tx.clean_merchant] += tx.amount

    analysis_rows = [["--- SPEND BY CATEGORY ---", ""], ["Category", "Total Amount"]]
    for cat, total in sorted(category_totals.items(), key=lambda x: x[1], reverse=True):
        analysis_rows.append([cat, round(total, 2)])

    analysis_rows.extend([["", ""], ["--- TOP MERCHANTS BY SPEND ---", ""], ["Merchant", "Total Amount"]])
    for merch, total in sorted(merchant_totals.items(), key=lambda x: x[1], reverse=True):
        analysis_rows.append([merch, round(total, 2)])

    analysis_ws.append_rows(analysis_rows)

    print(f"Exported run to sheets: [{summary_tab}], [{details_tab}], and [{analysis_tab}]")