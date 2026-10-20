import unittest
from datetime import date
from pathlib import Path
from typing import NamedTuple

from src.adapters.first_gingham import FirstGinghamAdapter
from src.adapters.statement import StatementAdapter
from src.download_accounts import DownloadAccount
from src.downloads import Coverage
from src.export_window import ExportWindow
from src.schemas import AccountSummary, ParsedTransaction

FIXTURES = Path(__file__).parent / "fixtures" / "downloads"
WINDOW = ExportWindow(start=date(2026, 9, 18), end=date(2026, 10, 19))


class ContractCase(NamedTuple):
    """One adapter's fixture, with what parsing it over WINDOW must give, known from reading the fixture."""
    name: str
    adapter: object
    file: Path
    account: DownloadAccount
    dates: list[str]  # Every Transaction's date, in order
    coverage: Coverage
    pending: list[str]  # Descriptions of pending activity in the file


GINGHAM = FirstGinghamAdapter()


def gemini_statement(path, force_reprocess=False) -> AccountSummary:
    """A Statement PDF as Gemini parses it: amounts are positive for money out on every Account kind."""
    return AccountSummary(
        institution="First Platypus Bank", account_name="Platypus Rewards - 1357", period="Sep 12, 2026 - Oct 11, 2026",
        # Gemini guesses the Account kind, and can guess wrong; the Download Account config decides it
        period_start="2026-09-12", period_end="2026-10-11", account_kind="deposit", balance=640.10,
        amount_due=35.00, due_date="2026-11-05",
        transactions=[
            ParsedTransaction(transaction_date=day, description=desc, clean_merchant=desc, amount=amount, category=category)
            for day, desc, amount, category in [
                ("2026-09-14", "PINECONE MARKET #12", 22.40, "Groceries"),
                ("2026-09-21", "BEAN COUNTER COFFEE", 6.50, "Restaurants & Fast Food"),
                ("2026-10-02", "AUTOPAY PAYMENT", -300.00, "Payments & Credits"),
                ("2026-10-09", "PINECONE MARKET #12 RETURN", -12.00, "Groceries"),
            ]
        ],
    )


def contract_cases() -> list[ContractCase]:
    return [
        ContractCase(
            name="First Gingham card",
            adapter=GINGHAM,
            file=FIXTURES / "first_gingham_card.csv",
            account=DownloadAccount(adapter="first_gingham", institution=GINGHAM.institution, last4="4321", kind="liability"),
            dates=["2026-09-20", "2026-09-24", "2026-09-30", "2026-10-03", "2026-10-08"],
            # The file ends early, on 2026-10-12, and starts before the window
            coverage=Coverage(start=date(2026, 9, 18), end=date(2026, 10, 12)),
            pending=["FUEL DEPOT 88"],
        ),
        ContractCase(
            name="Statement",
            adapter=StatementAdapter(parse=gemini_statement),
            file=Path("input/platypus-2026-10.pdf"),
            account=DownloadAccount(adapter="statement", institution="First Platypus Bank", last4="1357", kind="liability"),
            dates=["2026-09-21", "2026-10-02", "2026-10-09"],
            # The Statement period, 2026-09-12 to 2026-10-11, clipped to the window
            coverage=Coverage(start=date(2026, 9, 18), end=date(2026, 10, 11)),
            pending=[],
        ),
        ContractCase(
            name="First Gingham checking",
            adapter=GINGHAM,
            file=FIXTURES / "first_gingham_checking.csv",
            account=DownloadAccount(adapter="first_gingham", institution=GINGHAM.institution, last4="8765", kind="deposit"),
            dates=["2026-09-19", "2026-10-01", "2026-10-03", "2026-10-15"],
            # The file runs past the window's end, as for an Export window that ends in the past
            coverage=Coverage(start=date(2026, 9, 18), end=date(2026, 10, 19)),
            pending=["FUEL DEPOT 88"],
        ),
    ]


class AdapterContractTest(unittest.TestCase):
    """What every adapter's parse must do, over every adapter's fixtures."""

    def parsed(self, case: ContractCase):
        return case.adapter.parse(case.file, case.account, WINDOW)

    def test_the_displayed_amount_sign_follows_account_kind(self):
        # ADR-0001: money out is positive on a Liability and negative on a Deposit account
        for case in contract_cases():
            with self.subTest(case.name):
                money_out_sign = 1 if case.account.kind == "liability" else -1
                for tx in self.parsed(case).summary.transactions:
                    money_out = tx.spend() if tx.spend() is not None else -tx.income() if tx.income() is not None else None
                    if money_out:
                        self.assertEqual(tx.amount, money_out_sign * money_out, tx.description)

    def test_pending_activity_is_not_a_transaction(self):
        for case in contract_cases():
            with self.subTest(case.name):
                descriptions = [tx.description for tx in self.parsed(case).summary.transactions]
                self.assertFalse(set(case.pending) & set(descriptions))

    def test_only_transactions_inside_the_export_window(self):
        for case in contract_cases():
            with self.subTest(case.name):
                self.assertEqual([tx.transaction_date for tx in self.parsed(case).summary.transactions], case.dates)

    def test_coverage_is_what_the_file_spans_within_the_window(self):
        for case in contract_cases():
            with self.subTest(case.name):
                self.assertEqual(self.parsed(case).coverage, case.coverage)

    def test_the_download_is_named_by_the_accounts_institution_and_last_four_digits(self):
        # Every Transaction's Details row takes its Institution and Account from the summary
        for case in contract_cases():
            with self.subTest(case.name):
                summary = self.parsed(case).summary
                self.assertEqual((summary.institution, summary.account_name), (case.account.institution, f"••{case.account.last4}"))
                self.assertEqual(summary.account_kind, case.account.kind)

    def test_recognizes_its_file_for_its_account_only(self):
        for case in contract_cases():
            with self.subTest(case.name):
                other_account = case.account.model_copy(update={"last4": "0000"})
                self.assertTrue(case.adapter.recognizes(case.file, case.account))
                self.assertFalse(case.adapter.recognizes(case.file, other_account))
                self.assertFalse(case.adapter.recognizes(FIXTURES / "not_gingham.csv", case.account))


if __name__ == "__main__":
    unittest.main()
