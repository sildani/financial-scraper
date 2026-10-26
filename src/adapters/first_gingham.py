import csv
from datetime import date
from pathlib import Path
from typing import List, Protocol

from src.assisted_downloads import Fetch
from src.download_accounts import DownloadAccount
from src.downloads import Coverage, Download, download
from src.export_window import ExportWindow
from src.llm import CategoryHint, categorize_download
from src.schemas import (
    AccountKind,
    SpendingCategory,
    Transaction,
    category_spend_and_income,
)

# The reference adapter: a made-up Institution's CSV, parsed in code, as the worked example for vendor adapters.
# Its file starts with a preamble, then one row per Transaction:
#
#   First Gingham Credit Union
#   Account ending,4321
#   From,2026-09-15,To,2026-10-12
#   Balance,-812.40                         (optional, as of To)
#   Date,Description,Category,Amount,Status
#   2026-09-20,PINECONE MARKET #12,Groceries,-60.00,Posted
#
# Amount is negative for money out on every Account kind, and Status is Posted or Pending. Its category
# column is only a hint: Gemini assigns each Transaction its Category (GLOSSARY.md).
HEADER = ["Date", "Description", "Category", "Amount", "Status"]


class Categorize(Protocol):
    def __call__(self, path: str, hints: List[CategoryHint], account_kind: AccountKind, force_reprocess: bool = False) -> List[SpendingCategory]: ...


class FirstGinghamAdapter:
    institution = "First Gingham Credit Union"

    def __init__(self, categorize: Categorize = categorize_download, force: bool = False):
        self._categorize, self.force = categorize, force

    def fetch(self, window: ExportWindow, account: DownloadAccount) -> Fetch:
        return Fetch(
            url="https://online.firstgingham.example/accounts/export",
            instructions=f"Choose CSV, {window.start.isoformat()} to {window.end.isoformat()}",
        )

    def recognizes(self, file: Path, account: DownloadAccount) -> bool:
        if file.suffix.lower() != ".csv":
            return False
        with open(file, newline="") as f:
            preamble = [row for _, row in zip(range(2), csv.reader(f))]
        return preamble == [[self.institution], ["Account ending", account.last4]]

    def parse(self, file: Path, account: DownloadAccount, window: ExportWindow) -> Download:
        with open(file, newline="") as f:
            rows = list(csv.reader(f))
        header = rows.index(HEADER)
        preamble = {row[0]: row[1:] for row in rows[1:header]}
        from_date, _, to_date = preamble["From"]
        span = Coverage(start=date.fromisoformat(from_date), end=date.fromisoformat(to_date))
        balance = float(preamble["Balance"][0]) if "Balance" in preamble else None

        # Pending activity is not a Transaction (GLOSSARY.md)
        posted = [(tx_date, description, vendor_category, -float(amount)) for tx_date, description, vendor_category, amount, status in rows[header + 1:] if status != "Pending"]
        # Every posted row, in the window or not, so the cached Categories hold for any window
        categories = self._categorize(str(file), [
            CategoryHint(description=description, vendor_category=vendor_category, is_money_out=money_out > 0)
            for _, description, vendor_category, money_out in posted
        ], account.kind, force_reprocess=self.force)

        transactions = []
        for (tx_date, description, _, money_out), category in zip(posted, categories, strict=True):
            spend, income = category_spend_and_income(category, money_out)
            transactions.append(Transaction(
                transaction_date=tx_date,
                description=description,
                clean_merchant=description,
                # The Displayed amount follows the Account's kind (ADR-0001)
                amount=-money_out if account.kind == "deposit" else money_out,
                category=category,
                spend_amount=spend,
                income_amount=income,
            ))
        return download(account, span, transactions, window, balance)
