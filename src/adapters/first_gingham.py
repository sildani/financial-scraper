import csv
from datetime import date
from pathlib import Path

from src.assisted_downloads import Fetch
from src.download_accounts import DownloadAccount
from src.downloads import Coverage, Download, download
from src.export_window import ExportWindow
from src.schemas import SpendingCategory, Transaction, category_spend_and_income

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
# Amount is negative for money out on every Account kind, and Status is Posted or Pending.
HEADER = ["Date", "Description", "Category", "Amount", "Status"]

# Until Categories come from Gemini, its own category column maps to ours; anything else is Other
CATEGORY_MAPPING = {
    "Groceries": SpendingCategory.GROCERIES,
    "Dining": SpendingCategory.RESTAURANTS_FAST_FOOD,
    "Gas": SpendingCategory.CAR_FUEL,
    "Auto": SpendingCategory.CAR_MAINTENANCE,
    "Travel": SpendingCategory.TRAVEL,
    "Bills": SpendingCategory.UTILITIES_SUBSCRIPTIONS,
    "Payment": SpendingCategory.PAYMENTS_CREDITS,
    "Transfer": SpendingCategory.PAYMENTS_CREDITS,
    "Deposit": SpendingCategory.INCOME_DEPOSITS,
    "Loan": SpendingCategory.MORTGAGE_LOANS,
}


class FirstGinghamAdapter:
    institution = "First Gingham Credit Union"

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

        transactions = []
        for row in rows[header + 1:]:
            tx_date, description, vendor_category, amount, status = row
            # Pending activity is not a Transaction (GLOSSARY.md)
            if status == "Pending":
                continue
            money_out = -float(amount)
            category = CATEGORY_MAPPING.get(vendor_category, SpendingCategory.OTHER)
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
