# The automated part of Phase 1 in docs/plaid-testing.md: a full Export, then a Partial Export, against
# fixture Links in the Plaid Sandbox, read back from the Sandbox Sheet. Opt-in, since it calls Plaid and Google:
#
#   PLAID_INTEGRATION=1 python -m unittest tests.integration.test_plaid_sandbox -v
#
# Its Links live only in memory, so your own Sandbox token store is never read or changed.
import hashlib
import json
import os
import re
import time
import unittest
from datetime import date

import plaid
from gspread.utils import ValueRenderOption

from main import run_export
from src.config import BASE_DIR, current_plaid_environment
from src.export_window import export_window
from src.plaid_client import get_plaid_client, get_transactions
from src.sheets import get_gspread_client
from scripts.seed_sandbox import load_links, create_fixture_link, remove_link, force_relink

READY_TIMEOUT_S = 60
PRODUCTION_TOKEN_FILE = BASE_DIR / ".plaid_tokens.production.json"

# Details rows as (Institution, Account, Displayed amount, Category, Spend), from docs/plaid-testing.md Step 4.
# Descriptions are left out: Plaid rewrites them (CHIPOTLE comes back as "Chipotle Mexican Grill").
# Spend is "" when the Transaction isn't Spend.
PLATYPUS, GINGHAM, TARTAN = "First Platypus Bank", "First Gingham Credit Union", "Tartan Bank"
FULL_DETAILS = [
    (PLATYPUS, "Checking", 2500, "Income & Deposits", ""),        # ACME Corp payroll: Income
    (PLATYPUS, "Checking", -85, "Groceries", 85),                 # Trader Joe's
    (PLATYPUS, "Checking", -500, "Payments & Credits", ""),       # Card Payment to First Gingham, paired
    (PLATYPUS, "Checking", -150, "Payments & Credits", ""),       # Discover e-payment, unpaired
    (PLATYPUS, "Checking", -300, "Payments & Credits", ""),       # Internal Transfer to savings, paired
    (PLATYPUS, "Checking", -80, "Other", 80),                     # Zelle to John Doe: Transfer Out
    (PLATYPUS, "Checking", -200, "Mortgage & Loans", 200),        # Toyota Financial: loan payment, paired
    (PLATYPUS, "Savings", 300, "Payments & Credits", ""),         # Internal Transfer from checking, paired
    (PLATYPUS, "Auto loan", -200, "Payments & Credits", ""),      # Loan payment counterpart, not Spend
    (GINGHAM, "Credit card", 60, "Groceries", 60),                # Whole Foods
    (GINGHAM, "Credit card", 40, "Restaurants & Fast Food", 40),  # Olive Garden
    (GINGHAM, "Credit card", 400, "Travel", 400),                 # Delta Air Lines
    (GINGHAM, "Credit card", 5, "Restaurants & Fast Food", 5),    # Starbucks
    (GINGHAM, "Credit card", -500, "Payments & Credits", ""),     # Card Payment received, paired
    (GINGHAM, "Credit card", -25, "Groceries", -25),              # Whole Foods Refund: negative Spend
    (GINGHAM, "Credit card", 12, "Other", 12),                    # ZQX Holdings
    (TARTAN, "Checking", -14, "Restaurants & Fast Food", 14),     # Chipotle
    (TARTAN, "Checking", -52, "Groceries", 52),                   # Safeway
]
FULL_SPEND_BY_CATEGORY = {
    "Travel": 400, "Mortgage & Loans": 200, "Groceries": 172, "Other": 92, "Restaurants & Fast Food": 59,
}
# Step 5: Tartan Bank needs re-link, so its Chipotle and Safeway drop out
PARTIAL_SPEND_BY_CATEGORY = {**FULL_SPEND_BY_CATEGORY, "Groceries": 120, "Restaurants & Fast Food": 45}


def file_digest(path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def without_mask(account_name: str) -> str:
    # Plaid assigns random masks, e.g. "Checking (4380)"
    return re.sub(r" \(\d+\)$", "", account_name)


class SheetExport:
    """The tabs one Export wrote, read back from the Sheet."""

    def __init__(self, spreadsheet, tabs: list[str]):
        self.tabs = {}
        for title in tabs:
            rows = spreadsheet.worksheet(title).get_all_values(value_render_option=ValueRenderOption.unformatted)
            self.tabs[title.split("_")[0]] = rows

    def banner(self, tab: str) -> list | None:
        first = [cell for cell in self.tabs[tab][0] if cell != ""]
        return first if first[0].startswith("INCOMPLETE") else None

    def table(self, tab: str) -> list[list]:
        """The tab's rows below its header, skipping any banner."""
        rows = self.tabs[tab][1:]
        return rows[1:] if self.banner(tab) else rows

    def details(self) -> list[tuple]:
        return [
            (institution, without_mask(account), amount, category, spend)
            for institution, account, _date, _description, _merchant, amount, category, spend, *_ in self.table("Details")
        ]

    def summary(self) -> list[tuple]:
        """(Institution, Account, Balance) per Account."""
        return [(row[0], without_mask(row[1]), row[3]) for row in self.table("Summary")]

    def analysis_section(self, heading: str) -> dict:
        rows = self.tabs["Analysis"]
        start = next(i for i, row in enumerate(rows) if row[0] == heading)
        section = {}
        for row in rows[start + 1:]:
            if row[0] == "" or row[0].startswith("---"):
                break
            section[row[0]] = row[1]
        return section

    def spend_by_category(self) -> dict:
        section = self.analysis_section("--- SPEND BY CATEGORY ---")
        section.pop("Category")
        section.pop("Total Spend")
        return section

    def total_spend(self) -> float:
        return self.analysis_section("--- SPEND BY CATEGORY ---")["Total Spend"]

    def income(self) -> float:
        return self.analysis_section("--- INCOME (not netted against Spend) ---")["Income"]

    def merchant_spend(self, merchant: str) -> float:
        return self.analysis_section("--- TOP MERCHANTS BY SPEND ---")[merchant]

    def linked_movements(self) -> list[tuple]:
        """(Status, Amount, Paid Description) per Linked Movement."""
        return [(row[0], row[1], row[5]) for row in self.table("LinkedMovements")]


@unittest.skipUnless(os.getenv("PLAID_INTEGRATION") == "1", "set PLAID_INTEGRATION=1 to run against the Plaid Sandbox")
@unittest.skipUnless(os.getenv("PLAID_ENV") == "sandbox", "runs only with PLAID_ENV=sandbox")
class PlaidSandboxExportTest(unittest.TestCase):
    keep_tabs = False  # Set once anything fails, so the Sheet shows what went wrong

    @classmethod
    def setUpClass(cls):
        sandbox_sheet, production_sheet = os.getenv("SPREADSHEET_ID_SANDBOX"), os.getenv("SPREADSHEET_ID_PRODUCTION")
        if sandbox_sheet == production_sheet:
            raise RuntimeError("SPREADSHEET_ID_SANDBOX is the same as SPREADSHEET_ID_PRODUCTION; refusing to write to it")
        cls.environment = current_plaid_environment()
        cls.production_tokens_before = file_digest(PRODUCTION_TOKEN_FILE)

        client = get_plaid_client()
        spreadsheet = get_gspread_client().open_by_key(cls.environment.spreadsheet_id)
        cls.addClassCleanup(cls.delete_tabs_unless_failed, spreadsheet)
        try:
            cls.tokens, token_by_link, cls.item_by_link = {}, {}, {}
            for name, link in load_links().items():
                item_id, access_token = create_fixture_link(client, link)
                cls.addClassCleanup(remove_link, client, access_token)
                cls.tokens[item_id] = token_by_link[name] = access_token
                cls.item_by_link[name] = item_id
            window = export_window(None, None, date.today())
            for access_token in cls.tokens.values():
                wait_until_ready(client, access_token, window)

            cls.full = run_export(cls.tokens, [], window, [], {}, spreadsheet=spreadsheet)
            cls.tabs = list(cls.full.tabs)
            cls.full_sheet = SheetExport(spreadsheet, cls.full.tabs)

            force_relink(client, token_by_link["tartan"])
            cls.partial = run_export(cls.tokens, [], window, [], {}, spreadsheet=spreadsheet)
            cls.tabs += cls.partial.tabs
            cls.partial_sheet = SheetExport(spreadsheet, cls.partial.tabs)
        except Exception:
            cls.keep_tabs = True
            raise

    @classmethod
    def delete_tabs_unless_failed(cls, spreadsheet):
        if cls.keep_tabs:
            print(f"\nKept the test's tabs in the Sandbox Sheet: {', '.join(getattr(cls, 'tabs', []))}")
            return
        for title in cls.tabs:
            spreadsheet.del_worksheet(spreadsheet.worksheet(title))

    def run(self, result=None):
        result = super().run(result)
        if result is not None and not result.wasSuccessful():
            type(self).keep_tabs = True
        return result

    # --- Step 4: full Export ---

    def test_details_have_every_displayed_amount_and_treatment(self):
        # Pending Shell and Uber, and Target from 35 days ago, are absent because they aren't listed
        self.assertCountEqual(self.full_sheet.details(), FULL_DETAILS)

    def test_three_linked_movements_pair_and_only_the_discover_payment_is_flagged(self):
        movements = self.full_sheet.linked_movements()
        self.assertCountEqual([m[1] for m in movements if m[0] == "Paired"], [500, 300, 200])
        self.assertEqual([m for m in movements if m[0] == "UNPAIRED"], [("UNPAIRED", 150, "DISCOVER E-PAYMENT")])

    def test_analysis_totals_spend_never_displayed_amount(self):
        self.assertEqual(self.full_sheet.spend_by_category(), FULL_SPEND_BY_CATEGORY)
        self.assertEqual(self.full_sheet.total_spend(), 923)
        self.assertEqual(self.full_sheet.income(), 2500)
        self.assertEqual(self.full_sheet.merchant_spend("Whole Foods"), 35)

    def test_summary_lists_every_account_with_its_balance(self):
        self.assertCountEqual(self.full_sheet.summary(), [
            (PLATYPUS, "Checking", 4200),
            (PLATYPUS, "Savings", 10300),
            (PLATYPUS, "Savings 2", 5000),  # Dormant: no Transactions, still listed
            (PLATYPUS, "Auto loan", 9800),
            (GINGHAM, "Credit card", 1250),
            (TARTAN, "Checking", 800),
        ])

    def test_full_export_is_not_marked_incomplete(self):
        self.assertEqual(self.full.fetched.failed, [])
        self.assertIsNone(self.full_sheet.banner("Summary"))
        self.assertIsNone(self.full_sheet.banner("Analysis"))

    # --- Step 5: Partial Export ---

    def test_partial_export_is_marked_incomplete_naming_tartan(self):
        # Sandbox makes a new Item ID each run, so the Link ID is its first 6 characters
        banner = [f"INCOMPLETE: missing {TARTAN} [{self.item_by_link['tartan'][:6]}]"]
        self.assertEqual(self.partial_sheet.banner("Summary"), banner)
        self.assertEqual(self.partial_sheet.banner("Analysis"), banner)

    def test_partial_export_reports_tartan_needs_relink(self):
        self.assertEqual([(link.institution, link.needs_relink) for link in self.partial.fetched.failed], [(TARTAN, True)])

    def test_partial_export_drops_only_tartan(self):
        self.assertCountEqual(self.partial_sheet.details(), [row for row in FULL_DETAILS if row[0] != TARTAN])
        self.assertNotIn(TARTAN, [row[0] for row in self.partial_sheet.summary()])
        self.assertEqual(self.partial_sheet.spend_by_category(), PARTIAL_SPEND_BY_CATEGORY)
        self.assertEqual(self.partial_sheet.total_spend(), 857)

    # --- Step 7: Environment isolation ---

    def test_writes_only_to_the_sandbox_sheet(self):
        self.assertEqual(self.environment.spreadsheet_id, os.getenv("SPREADSHEET_ID_SANDBOX"))

    def test_production_token_store_is_untouched(self):
        self.assertEqual(file_digest(PRODUCTION_TOKEN_FILE), self.production_tokens_before)


def wait_until_ready(client, access_token, window):
    """A new Sandbox Link returns PRODUCT_NOT_READY until Plaid has pulled its Transactions."""
    deadline = time.time() + READY_TIMEOUT_S
    while True:
        try:
            get_transactions(access_token, window.start, window.end, client=client)
            return
        except plaid.ApiException as e:
            if json.loads(e.body).get("error_code") != "PRODUCT_NOT_READY" or time.time() > deadline:
                raise
        time.sleep(2)


if __name__ == "__main__":
    unittest.main()
