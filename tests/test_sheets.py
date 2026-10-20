import contextlib
import io
import unittest
from datetime import date

import gspread

from src.linked_movements import LinkedMovements, MovementSide
from src.plaid_transformer import summarize_link
from src.schemas import AccountSummary, SpendingCategory, Transaction
from src.sheets import export_to_sheet
from tests.test_analysis import sandbox_gingham
from tests.test_plaid_transformer import NO_MOVEMENTS, WINDOW


class FakeWorksheet:
    def __init__(self):
        self.rows = []

    def append_rows(self, rows):
        self.rows.extend(rows)


class FakeSpreadsheet:
    """gspread's Spreadsheet, keeping each tab's rows keyed by tab name without its timestamp."""

    def __init__(self):
        self.tabs = {}

    def add_worksheet(self, title, rows, cols):
        worksheet = FakeWorksheet()
        self.tabs[title.split("_")[0]] = worksheet
        return worksheet


def gingham_summaries():
    return summarize_link(sandbox_gingham(), WINDOW, NO_MOVEMENTS)


def transaction(day, description, merchant, amount, category, spend=None, income=None):
    return Transaction(
        transaction_date=f"2026-10-{day:02d}", description=description, clean_merchant=merchant,
        amount=amount, category=category, spend_amount=spend, income_amount=income,
    )


class FakeResponse:
    """The HTTP response gspread wraps in an APIError."""

    def __init__(self, status_code):
        self.status_code = status_code
        self.text = ""

    def json(self):
        return {"error": {"code": self.status_code, "message": "Sheets error", "status": "ERROR"}}


class FlakySpreadsheet(FakeSpreadsheet):
    """A spreadsheet whose add_worksheet fails with status_code on the calls numbered in failing_calls (from 1)."""

    def __init__(self, status_code, failing_calls):
        super().__init__()
        self.status_code = status_code
        self.failing_calls = failing_calls
        self.titles = []

    def add_worksheet(self, title, rows, cols):
        self.titles.append(title.split("_")[0])
        if len(self.titles) in self.failing_calls:
            raise gspread.exceptions.APIError(FakeResponse(self.status_code))  # pyright: ignore[reportArgumentType]  # Stands in for requests' Response
        return super().add_worksheet(title, rows, cols)


def two_link_summaries():
    """A card at one Institution and checking at another, as Plaid summarizes them."""
    card = AccountSummary(
        institution="First Gingham Credit Union", account_name="Credit card (9496)", period="2026-09-17 to 2026-10-17",
        account_kind="liability", balance=1250.0, transactions=[
            transaction(1, "PINECONE MARKET #12", "Pinecone Market", 60.0, SpendingCategory.GROCERIES, spend=60.0),
            transaction(3, "BEAN COUNTER COFFEE", "Bean Counter Coffee", 5.0, SpendingCategory.RESTAURANTS_FAST_FOOD, spend=5.0),
            transaction(5, "PAYMENT THANK YOU", "Payment", -500.0, SpendingCategory.PAYMENTS_CREDITS),
            transaction(9, "ONLINE PAYMENT - THANK YOU", "Payment", -75.0, SpendingCategory.PAYMENTS_CREDITS),
        ],
    )
    checking = AccountSummary(
        institution="First Platypus Bank", account_name="Checking (7829)", period="2026-09-17 to 2026-10-17",
        account_kind="deposit", balance=3000.0, transactions=[
            transaction(2, "THISTLE GROCERY", "Thistle Grocery", -85.0, SpendingCategory.GROCERIES, spend=85.0),
            transaction(4, "PAYMENT TO FIRST GINGHAM CARD", "Card payment", -500.0, SpendingCategory.PAYMENTS_CREDITS),
            transaction(8, "WOMBAT CARD E-PAYMENT", "Card payment", -150.0, SpendingCategory.PAYMENTS_CREDITS),
            transaction(15, "ACME CORP PAYROLL", "ACME Corp", 2500.0, SpendingCategory.INCOME_DEPOSITS, income=2500.0),
        ],
    )
    return [card, checking]


def movement_side(institution, account_name, day, description, amount, direction):
    return MovementSide(
        transaction_id=f"tx-{description}", institution=institution, account_name=account_name,
        date=date(2026, 10, day), description=description, amount=amount, direction=direction,
    )


TWO_LINK_MOVEMENTS = LinkedMovements(
    paired=[(
        movement_side("First Platypus Bank", "Checking (7829)", 4, "PAYMENT TO FIRST GINGHAM CARD", 500.0, "paid"),
        movement_side("First Gingham Credit Union", "Credit card (9496)", 5, "PAYMENT THANK YOU", 500.0, "received"),
    )],
    unpaired=[
        movement_side("First Platypus Bank", "Checking (7829)", 8, "WOMBAT CARD E-PAYMENT", 150.0, "paid"),
        movement_side("First Gingham Credit Union", "Credit card (9496)", 9, "ONLINE PAYMENT - THANK YOU", 75.0, "received"),
    ],
)

SUMMARY_ROWS = [
    ["Institution", "Account Name", "Period", "Balance", "Amount Due", "Due Date", "Interest Charged", "Transaction Count"],
    ["First Gingham Credit Union", "Credit card (9496)", "2026-09-17 to 2026-10-17", 1250.0, None, None, 0.0, 4],
    ["First Platypus Bank", "Checking (7829)", "2026-09-17 to 2026-10-17", 3000.0, None, None, 0.0, 4],
]

DETAILS_ROWS = [
    ["Institution", "Account Name", "Date", "Raw Description", "Clean Merchant", "Amount", "Category", "Spend"],
    ["First Gingham Credit Union", "Credit card (9496)", "2026-10-01", "PINECONE MARKET #12", "Pinecone Market", 60.0, "Groceries", 60.0],
    ["First Gingham Credit Union", "Credit card (9496)", "2026-10-03", "BEAN COUNTER COFFEE", "Bean Counter Coffee", 5.0, "Restaurants & Fast Food", 5.0],
    ["First Gingham Credit Union", "Credit card (9496)", "2026-10-05", "PAYMENT THANK YOU", "Payment", -500.0, "Payments & Credits", ""],
    ["First Gingham Credit Union", "Credit card (9496)", "2026-10-09", "ONLINE PAYMENT - THANK YOU", "Payment", -75.0, "Payments & Credits", ""],
    ["First Platypus Bank", "Checking (7829)", "2026-10-02", "THISTLE GROCERY", "Thistle Grocery", -85.0, "Groceries", 85.0],
    ["First Platypus Bank", "Checking (7829)", "2026-10-04", "PAYMENT TO FIRST GINGHAM CARD", "Card payment", -500.0, "Payments & Credits", ""],
    ["First Platypus Bank", "Checking (7829)", "2026-10-08", "WOMBAT CARD E-PAYMENT", "Card payment", -150.0, "Payments & Credits", ""],
    ["First Platypus Bank", "Checking (7829)", "2026-10-15", "ACME CORP PAYROLL", "ACME Corp", 2500.0, "Income & Deposits", ""],
]

ANALYSIS_ROWS = [
    ["--- SPEND BY CATEGORY ---", ""], ["Category", "Spend"],
    ["Groceries", 145.0], ["Restaurants & Fast Food", 5.0],
    ["Total Spend", 150.0],
    ["", ""], ["--- INCOME (not netted against Spend) ---", ""], ["Income", 2500.0],
    ["", ""], ["--- TOP MERCHANTS BY SPEND ---", ""], ["Merchant", "Spend"],
    ["Thistle Grocery", 85.0], ["Pinecone Market", 60.0], ["Bean Counter Coffee", 5.0],
]

LINKED_MOVEMENTS_ROWS = [
    ["Status", "Amount",
     "Paid From Institution", "Paid From Account", "Paid Date", "Paid Description",
     "Received By Institution", "Received By Account", "Received Date", "Received Description"],
    ["Paired", 500.0,
     "First Platypus Bank", "Checking (7829)", "2026-10-04", "PAYMENT TO FIRST GINGHAM CARD",
     "First Gingham Credit Union", "Credit card (9496)", "2026-10-05", "PAYMENT THANK YOU"],
    ["UNPAIRED", 150.0, "First Platypus Bank", "Checking (7829)", "2026-10-08", "WOMBAT CARD E-PAYMENT", "", "", "", ""],
    ["UNPAIRED", 75.0, "", "", "", "", "First Gingham Credit Union", "Credit card (9496)", "2026-10-09", "ONLINE PAYMENT - THANK YOU"],
]


def tab_rows(spreadsheet):
    return {name: worksheet.rows for name, worksheet in spreadsheet.tabs.items()}


class TabContentsTest(unittest.TestCase):
    def test_an_export_with_linked_movements_writes_every_tab(self):
        spreadsheet = FakeSpreadsheet()
        tabs = export_to_sheet(two_link_summaries(), TWO_LINK_MOVEMENTS, spreadsheet=spreadsheet)
        self.assertEqual(tab_rows(spreadsheet), {
            "Summary": SUMMARY_ROWS, "Details": DETAILS_ROWS, "Analysis": ANALYSIS_ROWS, "LinkedMovements": LINKED_MOVEMENTS_ROWS,
        })
        self.assertEqual([tab.split("_")[0] for tab in tabs], ["Summary", "Details", "Analysis", "LinkedMovements"])

    def test_a_partial_export_adds_only_the_banner(self):
        spreadsheet = FakeSpreadsheet()
        export_to_sheet(two_link_summaries(), TWO_LINK_MOVEMENTS, missing=["Tartan Bank", "First Wombat Bank"], spreadsheet=spreadsheet)
        banner = ["INCOMPLETE: missing Tartan Bank, First Wombat Bank"]
        self.assertEqual(tab_rows(spreadsheet), {
            "Summary": [banner, *SUMMARY_ROWS], "Details": DETAILS_ROWS, "Analysis": [banner, *ANALYSIS_ROWS], "LinkedMovements": LINKED_MOVEMENTS_ROWS,
        })

    def test_an_export_without_linked_movements_has_no_linked_movements_tab(self):
        spreadsheet = FakeSpreadsheet()
        tabs = export_to_sheet(two_link_summaries(), spreadsheet=spreadsheet)
        self.assertEqual(list(spreadsheet.tabs), ["Summary", "Details", "Analysis"])
        self.assertEqual([tab.split("_")[0] for tab in tabs], ["Summary", "Details", "Analysis"])


class RetryTest(unittest.TestCase):
    def export(self, spreadsheet):
        self.sleeps = []
        with contextlib.redirect_stdout(io.StringIO()):
            return export_to_sheet(two_link_summaries(), TWO_LINK_MOVEMENTS, spreadsheet=spreadsheet, sleep=self.sleeps.append)

    def test_a_503_part_way_through_retries_the_whole_export(self):
        spreadsheet = FlakySpreadsheet(503, failing_calls={2})
        self.export(spreadsheet)
        self.assertEqual(spreadsheet.titles, ["Summary", "Details", "Summary", "Details", "Analysis", "LinkedMovements"])
        self.assertEqual(spreadsheet.tabs["Details"].rows, DETAILS_ROWS)
        self.assertEqual(self.sleeps, [5])

    def test_three_503s_give_up_with_the_last_error(self):
        spreadsheet = FlakySpreadsheet(503, failing_calls={1, 2, 3})
        with self.assertRaises(gspread.exceptions.APIError):
            self.export(spreadsheet)
        self.assertEqual(spreadsheet.titles, ["Summary", "Summary", "Summary"])
        self.assertEqual(self.sleeps, [5, 5])

    def test_any_other_error_is_raised_without_retrying(self):
        spreadsheet = FlakySpreadsheet(500, failing_calls={1})
        with self.assertRaises(gspread.exceptions.APIError):
            self.export(spreadsheet)
        self.assertEqual(spreadsheet.titles, ["Summary"])
        self.assertEqual(self.sleeps, [])


class PartialExportTest(unittest.TestCase):
    def test_partial_export_is_marked_incomplete_naming_the_missing_institutions(self):
        spreadsheet = FakeSpreadsheet()
        export_to_sheet(gingham_summaries(), NO_MOVEMENTS, missing=["Tartan Bank"], spreadsheet=spreadsheet)
        banner = ["INCOMPLETE: missing Tartan Bank"]
        self.assertEqual(spreadsheet.tabs["Summary"].rows[0], banner)
        self.assertEqual(spreadsheet.tabs["Analysis"].rows[0], banner)

    def test_complete_export_is_not_marked(self):
        spreadsheet = FakeSpreadsheet()
        export_to_sheet(gingham_summaries(), NO_MOVEMENTS, spreadsheet=spreadsheet)
        self.assertEqual(spreadsheet.tabs["Summary"].rows[0][0], "Institution")
        self.assertEqual(spreadsheet.tabs["Analysis"].rows[0][0], "--- SPEND BY CATEGORY ---")


if __name__ == "__main__":
    unittest.main()
