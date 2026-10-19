from datetime import datetime, timezone
import time
from typing import NamedTuple
import gspread

from src.schemas import AccountSummary
from src.linked_movements import LinkedMovements, MovementSide
from src.analysis import analyze
from src.config import SERVICE_ACCOUNT_FILE, current_plaid_environment

SHEETS_ATTEMPTS = 3
SHEETS_RETRY_DELAY_S = 5
SHEETS_UNAVAILABLE_STATUS_CODE = 503


class Tab(NamedTuple):
    rows: list[list]
    size: tuple[int, int]  # (rows, cols) when added; appending past them grows the tab


def get_gspread_client() -> gspread.Client:
    return gspread.service_account(filename=SERVICE_ACCOUNT_FILE)


def open_export_spreadsheet():
    return get_gspread_client().open_by_key(current_plaid_environment().spreadsheet_id)


def export_to_sheet(summaries: list[AccountSummary], movements: LinkedMovements | None = None, missing: list[str] = (), spreadsheet=None, sleep=time.sleep):
    """Exports structured data to timestamped tabs:
    - Summary_YYYYMMDDHHMMSS
    - Details_YYYYMMDDHHMMSS
    - Analysis_YYYYMMDDHHMMSS
    - LinkedMovements_YYYYMMDDHHMMSS, only when movements are given (Plaid mode)
    A Partial Export names what is missing (each Link, or each Statement's PDF file) in a banner atop Summary and Analysis.
    Returns the names of the tabs it wrote.
    """
    banner = [[f"INCOMPLETE: missing {', '.join(missing)}"]] if missing else []
    tabs = {
        "Summary": Tab(summary_rows(summaries, banner), size=(100, 10)),
        "Details": Tab(details_rows(summaries), size=(2000, 10)),
        "Analysis": Tab(analysis_rows(summaries, banner), size=(500, 10)),
    }
    if movements is not None:
        tabs["LinkedMovements"] = Tab(linked_movements_rows(movements), size=(500, 12))

    # A 503 can come part way through, so a retry writes every tab again under a new timestamp
    titles = write_with_retries(lambda: write_tabs(spreadsheet or open_export_spreadsheet(), tabs), sleep)
    if movements is not None:
        print(f"Exported Linked Movements to [{titles['LinkedMovements']}]")
    print(f"Exported run to sheets: [{titles['Summary']}], [{titles['Details']}], and [{titles['Analysis']}]")
    return list(titles.values())


def write_with_retries(write, sleep=time.sleep):
    """write(), retried after a delay while Google Sheets is unavailable."""
    for attempt in range(1, SHEETS_ATTEMPTS + 1):
        try:
            return write()
        except gspread.exceptions.APIError as e:
            if e.response.status_code != SHEETS_UNAVAILABLE_STATUS_CODE or attempt == SHEETS_ATTEMPTS:
                print(f"   ✗ Failed to export to Google Sheets after {SHEETS_ATTEMPTS} attempts.")
                raise
            print(f"   ! Google Sheets API unavailable (503). Retrying in {SHEETS_RETRY_DELAY_S} seconds... ({attempt}/{SHEETS_ATTEMPTS})")
            sleep(SHEETS_RETRY_DELAY_S)


def write_tabs(spreadsheet, tabs: dict[str, Tab]) -> dict[str, str]:
    """Adds each tab to spreadsheet under one timestamp, with its rows; returns each tab's title by name."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    titles = {}
    for name, tab in tabs.items():
        titles[name] = f"{name}_{timestamp}"
        tab_rows, tab_cols = tab.size
        spreadsheet.add_worksheet(title=titles[name], rows=tab_rows, cols=tab_cols).append_rows(tab.rows)
    return titles


def summary_rows(summaries: list[AccountSummary], banner: list[list]) -> list[list]:
    return [
        *banner,
        ["Institution", "Account Name", "Period", "Balance", "Amount Due", "Due Date", "Interest Charged", "Transaction Count"],
        *([s.institution, s.account_name, s.period, s.balance, s.amount_due, s.due_date, s.interest_charged, len(s.transactions)] for s in summaries),
    ]


def details_rows(summaries: list[AccountSummary]) -> list[list]:
    return [
        ["Institution", "Account Name", "Date", "Raw Description", "Clean Merchant", "Amount", "Category", "Spend"],
        *(
            [
                s.institution, s.account_name, tx.transaction_date, tx.description, tx.clean_merchant, tx.amount, tx.category.value,
                "" if tx.spend() is None else tx.spend(),  # Blank when not Spend
            ]
            for s in summaries for tx in s.transactions
        ),
    ]


def analysis_rows(summaries: list[AccountSummary], banner: list[list]) -> list[list]:
    """Spend, never Displayed amount (ADR-0001)."""
    analysis = analyze(summaries)
    return [
        *banner,
        ["--- SPEND BY CATEGORY ---", ""], ["Category", "Spend"],
        *([cat, total] for cat, total in analysis.spend_by_category.items()),
        ["Total Spend", analysis.total_spend],
        ["", ""], ["--- INCOME (not netted against Spend) ---", ""], ["Income", analysis.income],
        ["", ""], ["--- TOP MERCHANTS BY SPEND ---", ""], ["Merchant", "Spend"],
        *([merch, total] for merch, total in analysis.spend_by_merchant.items()),
    ]


def linked_movements_rows(movements: LinkedMovements) -> list[list]:
    """Paired sides, then unpaired sides flagged."""
    no_side = ["", "", "", ""]
    return [
        [
            "Status", "Amount",
            "Paid From Institution", "Paid From Account", "Paid Date", "Paid Description",
            "Received By Institution", "Received By Account", "Received Date", "Received Description",
        ],
        *(["Paired", paid.amount, *side_cells(paid), *side_cells(received)] for paid, received in movements.paired),
        *(
            ["UNPAIRED", s.amount, *(side_cells(s) + no_side if s.direction == "paid" else no_side + side_cells(s))]
            for s in movements.unpaired
        ),
    ]


def side_cells(side: MovementSide) -> list:
    return [side.institution, side.account_name, side.date.isoformat(), side.description]
