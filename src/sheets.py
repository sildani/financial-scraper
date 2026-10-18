from datetime import datetime, timezone
import time
import gspread

from src.schemas import AccountSummary
from src.linked_movements import LinkedMovements, MovementSide
from src.analysis import analyze
from src.config import SERVICE_ACCOUNT_FILE, current_plaid_environment


def side_cells(side: MovementSide) -> list:
    return [side.institution, side.account_name, side.date.isoformat(), side.description]


def get_gspread_client() -> gspread.Client:
    return gspread.service_account(filename=SERVICE_ACCOUNT_FILE)


def export_statements_to_sheet(summaries: list[AccountSummary], movements: LinkedMovements | None = None, missing: list[str] = (), spreadsheet=None):
    """Exports structured data to three timestamped tabs:
    - Summary_YYYYMMDDHHMMSS
    - Details_YYYYMMDDHHMMSS
    - Analysis_YYYYMMDDHHMMSS
    plus LinkedMovements_YYYYMMDDHHMMSS when movements are given (Plaid mode only).
    A Partial Export names its missing Institutions in a banner atop Summary and Analysis.
    """
    MAX_RETRIES = 3
    RETRY_DELAY_S = 5
    banner = [f"INCOMPLETE: missing {', '.join(missing)}"] if missing else None

    for attempt in range(MAX_RETRIES):
        try:
            sh = spreadsheet or get_gspread_client().open_by_key(current_plaid_environment().spreadsheet_id)

            timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            
            summary_tab = f"Summary_{timestamp_str}"
            details_tab = f"Details_{timestamp_str}"
            analysis_tab = f"Analysis_{timestamp_str}"

            # 1. Summary Sheet
            summary_ws = sh.add_worksheet(title=summary_tab, rows=100, cols=10)
            if banner:
                summary_ws.append_row(banner)
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
                "Institution", "Account Name", "Date", "Raw Description", "Clean Merchant", "Amount", "Category", "Spend"
            ])
            details_rows = []
            for s in summaries:
                for tx in s.transactions:
                    details_rows.append([
                        s.institution, s.account_name, tx.transaction_date, tx.description, tx.clean_merchant, tx.amount, tx.category.value,
                        "" if tx.spend() is None else tx.spend(),  # Blank when not Spend
                    ])
            details_ws.append_rows(details_rows)

            # 3. Aggregated Analysis Sheet: Spend, never Displayed amount (ADR-0001)
            analysis_ws = sh.add_worksheet(title=analysis_tab, rows=500, cols=10)
            analysis = analyze(summaries)

            analysis_rows = [banner] if banner else []
            analysis_rows += [["--- SPEND BY CATEGORY ---", ""], ["Category", "Spend"]]
            analysis_rows.extend([cat, total] for cat, total in analysis.spend_by_category.items())
            analysis_rows.append(["Total Spend", analysis.total_spend])

            analysis_rows.extend([["", ""], ["--- INCOME (not netted against Spend) ---", ""], ["Income", analysis.income]])

            analysis_rows.extend([["", ""], ["--- TOP MERCHANTS BY SPEND ---", ""], ["Merchant", "Spend"]])
            analysis_rows.extend([merch, total] for merch, total in analysis.spend_by_merchant.items())

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
