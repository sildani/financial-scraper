import unittest
from datetime import date

from src.plaid_transformer import transform_plaid_transactions
from src.schemas import PlaidAccount, PlaidTransaction


def account(**overrides) -> PlaidAccount:
    fields = {
        "account_id": "acc-1",
        "balances": {"current": 1250.00, "iso_currency_code": "USD"},
        "mask": "9496",
        "name": "Credit card",
        "type": "credit",
        "subtype": "credit card",
    }
    return PlaidAccount(**{**fields, **overrides})


def transaction(name: str, **overrides) -> PlaidTransaction:
    fields = {
        "account_id": "acc-1",
        "amount": 10.00,
        "iso_currency_code": "USD",
        "date": date(2026, 10, 1),
        "name": name,
        "pending": False,
        "transaction_id": f"tx-{name}",
    }
    return PlaidTransaction(**{**fields, **overrides})


def export(transactions):
    summary = transform_plaid_transactions(transactions, account(), "2026-09-17", "2026-10-17")
    return [item.description for item in summary.transactions]


class PendingActivityTest(unittest.TestCase):
    def test_pending_activity_is_not_a_transaction(self):
        exported = export([
            transaction("Whole Foods"),
            transaction("Uber", pending=True),
        ])
        self.assertEqual(exported, ["Whole Foods"])

    def test_account_with_only_pending_activity_has_no_transactions(self):
        self.assertEqual(export([transaction("Uber", pending=True)]), [])


if __name__ == "__main__":
    unittest.main()
