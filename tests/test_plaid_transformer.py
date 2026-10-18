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


def export(transactions, **account_overrides):
    summary = transform_plaid_transactions(transactions, account(**account_overrides), "2026-09-17", "2026-10-17")
    return [item.description for item in summary.transactions]


def displayed_amounts(transactions, **account_overrides):
    summary = transform_plaid_transactions(transactions, account(**account_overrides), "2026-09-17", "2026-10-17")
    return {item.description: item.amount for item in summary.transactions}


class PendingActivityTest(unittest.TestCase):
    def test_pending_activity_is_not_a_transaction(self):
        exported = export([
            transaction("Whole Foods"),
            transaction("Uber", pending=True),
        ])
        self.assertEqual(exported, ["Whole Foods"])

    def test_account_with_only_pending_activity_has_no_transactions(self):
        self.assertEqual(export([transaction("Uber", pending=True)]), [])


class DisplayedAmountTest(unittest.TestCase):
    # Plaid's sign is positive for money out on every Account kind (ADR-0001)

    def test_deposit_account_shows_money_in_as_positive(self):
        amounts = displayed_amounts(
            [transaction("ACME Corp payroll", amount=-2500.00), transaction("Trader Joe's", amount=85.00)],
            type="depository", subtype="checking",
        )
        self.assertEqual(amounts, {"ACME Corp payroll": 2500.00, "Trader Joe's": -85.00})

    def test_credit_card_shows_money_out_as_positive(self):
        amounts = displayed_amounts(
            [transaction("Whole Foods", amount=60.00), transaction("Payment thank you", amount=-500.00)],
            type="credit", subtype="credit card",
        )
        self.assertEqual(amounts, {"Whole Foods": 60.00, "Payment thank you": -500.00})

    def test_loan_shows_money_out_as_positive(self):
        amounts = displayed_amounts(
            [transaction("Payment received", amount=-200.00)],
            type="loan", subtype="auto",
        )
        self.assertEqual(amounts, {"Payment received": -200.00})


if __name__ == "__main__":
    unittest.main()
