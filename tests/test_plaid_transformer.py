import unittest
from datetime import date

from src.plaid_transformer import transform_plaid_transactions
from src.schemas import PlaidAccount, PlaidTransaction, SpendingCategory


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


def category_for(plaid_detailed: str) -> SpendingCategory:
    tx = transaction("Merchant", personal_finance_category={"primary": plaid_detailed.split("_")[0], "detailed": plaid_detailed})
    summary = transform_plaid_transactions([tx], account(), "2026-09-17", "2026-10-17")
    return summary.transactions[0].category


class CategoryTest(unittest.TestCase):
    def assertCategories(self, expected: dict):
        for plaid_detailed, category in expected.items():
            with self.subTest(plaid_detailed=plaid_detailed):
                self.assertEqual(category_for(plaid_detailed), category)

    def test_plaid_categories_returned_by_sandbox_fixtures(self):
        # Exactly what Plaid returned for docs/plaid-testing.md Phase 1 (PFCv2)
        self.assertCategories({
            "FOOD_AND_DRINK_GROCERIES": SpendingCategory.GROCERIES,
            "FOOD_AND_DRINK_RESTAURANT": SpendingCategory.RESTAURANTS_FAST_FOOD,
            "FOOD_AND_DRINK_COFFEE": SpendingCategory.RESTAURANTS_FAST_FOOD,
            "TRAVEL_FLIGHTS": SpendingCategory.TRAVEL,
            "LOAN_PAYMENTS_CAR_PAYMENT": SpendingCategory.MORTGAGE_LOANS,
            "INCOME_SALARY": SpendingCategory.INCOME_DEPOSITS,
            "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT": SpendingCategory.PAYMENTS_CREDITS,
            "TRANSFER_OUT_TRANSFER_OUT_FROM_APPS": SpendingCategory.OTHER,
            "OTHER_OTHER": SpendingCategory.OTHER,
        })

    def test_other_current_plaid_categories(self):
        self.assertCategories({
            "INCOME_WAGES": SpendingCategory.INCOME_DEPOSITS,  # PFCv1 name for INCOME_SALARY
            "FOOD_AND_DRINK_FAST_FOOD": SpendingCategory.RESTAURANTS_FAST_FOOD,
            "TRAVEL_LODGING": SpendingCategory.TRAVEL,
            "TRANSPORTATION_GAS": SpendingCategory.CAR_FUEL,
            "GENERAL_SERVICES_AUTOMOTIVE": SpendingCategory.CAR_MAINTENANCE,
            "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY": SpendingCategory.UTILITIES_SUBSCRIPTIONS,
            "RENT_AND_UTILITIES_TELEPHONE": SpendingCategory.UTILITIES_SUBSCRIPTIONS,
            "GENERAL_SERVICES_INSURANCE": SpendingCategory.UTILITIES_SUBSCRIPTIONS,
            "LOAN_PAYMENTS_MORTGAGE_PAYMENT": SpendingCategory.MORTGAGE_LOANS,
            "LOAN_PAYMENTS_STUDENT_LOAN_PAYMENT": SpendingCategory.MORTGAGE_LOANS,
            "LOAN_PAYMENTS_PERSONAL_LOAN_PAYMENT": SpendingCategory.MORTGAGE_LOANS,
        })

    def test_categories_that_do_not_clearly_fit_go_to_other(self):
        # Folded in from #9
        self.assertCategories({
            "TRANSPORTATION_PUBLIC_TRANSIT": SpendingCategory.OTHER,
            "TRANSPORTATION_TAXIS_AND_RIDE_SHARES": SpendingCategory.OTHER,
            "GENERAL_MERCHANDISE_SUPERSTORES": SpendingCategory.OTHER,
            "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES": SpendingCategory.OTHER,
            "GENERAL_MERCHANDISE_DEPARTMENT_STORES": SpendingCategory.OTHER,
            "GENERAL_MERCHANDISE_CLOTHING_AND_ACCESSORIES": SpendingCategory.OTHER,
            "RENT_AND_UTILITIES_RENT": SpendingCategory.OTHER,
            "SOMETHING_PLAID_ADDS_LATER": SpendingCategory.OTHER,
        })


if __name__ == "__main__":
    unittest.main()
