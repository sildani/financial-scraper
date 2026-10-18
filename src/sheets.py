from datetime import datetime, timezone
from collections import defaultdict
import time
import gspread

from src.schemas import AccountSummary
from src.linked_movements import LinkedMovements, MovementSide
from src.config import SERVICE_ACCOUNT_FILE, current_plaid_environment


def side_cells(side: MovementSide) -> list:
    return [side.institution, side.account_name, side.date.isoformat(), side.description]


def get_gspread_client() -> gspread.Client:
    return gspread.service_account(filename=SERVICE_ACCOUNT_FILE)


def export_statements_to_sheet(summaries: list[AccountSummary], movements: LinkedMovements | None = None):
    """Exports structured data to three timestamped tabs:
    - Summary_YYYYMMDDHHMMSS
    - Details_YYYYMMDDHHMMSS
    - Analysis_YYYYMMDDHHMMSS
    plus LinkedMovements_YYYYMMDDHHMMSS when movements are given (Plaid mode only).
    """
    MAX_RETRIES = 3
    RETRY_DELAY_S = 5

    for attempt in range(MAX_RETRIES):
        try:
            gc = get_gspread_client()
            sh = gc.open_by_key(current_plaid_environment().spreadsheet_id)

            timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            
            summary_tab = f"Summary_{timestamp_str}"
            details_tab = f"Details_{timestamp_str}"
            analysis_tab = f"Analysis_{timestamp_str}"

            # 1. Summary Sheet
            summary_ws = sh.add_worksheet(title=summary_tab, rows=100, cols=10)
            summary_ws.append_row([
                "Institution", "Account Name", "Period",
                "Balance", "Amount Due", "Due Date", "Interest Charged", "Transaction Count"
            ])
            summary_rows = [
                [s.institution, s.account_name, s.period, s.balance, s.amount_due, s.due_date, s.interest_charged, len(s.transactions)]
                for s in summaries
            ]
            summary_ws.append_rows(summary_rows)

            # 2. Detailed Spending Sheet
            details_ws = sh.add_worksheet(title=details_tab, rows=2000, cols=10)
            details_ws.append_row([
                "Institution", "Account Name", "Date", "Raw Description", "Clean Merchant", "Amount", "Category"
            ])
            details_rows = []
            for s in summaries:
                for tx in s.transactions:
                    details_rows.append([
                        s.institution, s.account_name, tx.transaction_date, tx.description, tx.clean_merchant, tx.amount, tx.category.value
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

            # 4. Linked Movements Sheet: paired sides, then unpaired sides flagged
            if movements is not None:
                movements_tab = f"LinkedMovements_{timestamp_str}"
                movements_ws = sh.add_worksheet(title=movements_tab, rows=500, cols=12)
                movements_ws.append_row([
                    "Status", "Amount",
                    "Paid From Institution", "Paid From Account", "Paid Date", "Paid Description",
                    "Received By Institution", "Received By Account", "Received Date", "Received Description",
                ])
                no_side = ["", "", "", ""]
                movement_rows = [
                    ["Paired", paid.amount, *side_cells(paid), *side_cells(received)]
                    for paid, received in movements.paired
                ]
                movement_rows += [
                    ["UNPAIRED", s.amount, *(side_cells(s) + no_side if s.direction == "paid" else no_side + side_cells(s))]
                    for s in movements.unpaired
                ]
                movements_ws.append_rows(movement_rows)
                print(f"Exported Linked Movements to [{movements_tab}]")

            print(f"Exported run to sheets: [{summary_tab}], [{details_tab}], and [{analysis_tab}]")
            return # Success, exit the function and loop
        except gspread.exceptions.APIError as e:
            if e.response.status_code == 503 and attempt < MAX_RETRIES - 1:
                print(f"   ! Google Sheets API unavailable (503). Retrying in {RETRY_DELAY_S} seconds... ({attempt + 1}/{MAX_RETRIES})")
                time.sleep(RETRY_DELAY_S)
            else:
                print(f"   ✗ Failed to export to Google Sheets after {MAX_RETRIES} attempts.")
                raise # Re-raise the final exception
