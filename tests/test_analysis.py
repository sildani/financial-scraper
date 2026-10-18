import unittest

from src.analysis import analyze
from src.linked_movements import pair_linked_movements
from src.plaid_transformer import summarize_link
from tests.test_linked_movements import CHECKING, SAVINGS, AUTO_LOAN, CARD, side
from src.schemas import AccountSummary, LineItem
from tests.test_plaid_transformer import account, link_response


def plaid_analysis(*responses):
    movements = pair_linked_movements(list(responses))
    summaries = [
        summary
        for response in responses
        for summary in summarize_link(response, "2026-09-17", "2026-10-17", movements)
    ]
    return analyze(summaries)


class PlaidSpendTest(unittest.TestCase):
    def test_purchases_are_positive_spend_on_every_account_kind(self):
        analysis = plaid_analysis(link_response([CHECKING, CARD], [
            side("Trader Joe's", "acc-checking", 85.00, "FOOD_AND_DRINK_GROCERIES"),
            side("Whole Foods", "acc-card", 60.00, "FOOD_AND_DRINK_GROCERIES"),
        ]))
        self.assertEqual(analysis.spend_by_category, {"Groceries": 145.00})
        self.assertEqual(analysis.total_spend, 145.00)

    def test_income_is_reported_separately_and_never_netted(self):
        analysis = plaid_analysis(link_response([CHECKING], [
            side("ACME CORP PAYROLL", "acc-checking", -2500.00, "INCOME_SALARY"),
            side("Trader Joe's", "acc-checking", 85.00, "FOOD_AND_DRINK_GROCERIES"),
        ]))
        self.assertEqual(analysis.spend_by_category, {"Groceries": 85.00})
        self.assertEqual(analysis.total_spend, 85.00)
        self.assertEqual(analysis.income, 2500.00)

    def test_card_payments_and_internal_transfers_are_not_spend(self):
        analysis = plaid_analysis(link_response([CHECKING, SAVINGS, CARD], [
            side("PAYMENT TO FIRST GINGHAM CARD", "acc-checking", 500.00, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
            side("PAYMENT THANK YOU", "acc-card", -500.00, "TRANSFER_IN_OTHER_TRANSFER_IN"),
            side("DISCOVER E-PAYMENT", "acc-checking", 150.00, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
            side("ONLINE TRANSFER TO SAVINGS", "acc-checking", 300.00, "TRANSFER_OUT_SAVINGS"),
            side("ONLINE TRANSFER FROM CHECKING", "acc-savings", -300.00, "TRANSFER_IN_ACCOUNT_TRANSFER"),
        ]))
        self.assertEqual(analysis.spend_by_category, {})
        self.assertEqual((analysis.total_spend, analysis.income), (0.00, 0.00))

    def test_loan_payment_is_spend_on_the_paying_side_only(self):
        analysis = plaid_analysis(link_response([CHECKING, AUTO_LOAN], [
            side("TOYOTA FINANCIAL PAYMENT", "acc-checking", 200.00, "LOAN_PAYMENTS_CAR_PAYMENT"),
            side("PAYMENT RECEIVED THANK YOU", "acc-auto", -200.00, "TRANSFER_IN_OTHER_TRANSFER_IN"),
        ]))
        self.assertEqual(analysis.spend_by_category, {"Mortgage & Loans": 200.00})
        self.assertEqual(analysis.total_spend, 200.00)

    def test_loan_payment_to_an_unlinked_lender_is_still_spend(self):
        analysis = plaid_analysis(link_response([CHECKING], [
            side("ROCKET MORTGAGE", "acc-checking", 1800.00, "LOAN_PAYMENTS_MORTGAGE_PAYMENT"),
        ]))
        self.assertEqual(analysis.spend_by_category, {"Mortgage & Loans": 1800.00})

    def test_payment_into_a_liability_from_an_unlinked_account_is_not_spend(self):
        analysis = plaid_analysis(link_response([CARD], [
            side("PAYMENT THANK YOU", "acc-card", -500.00, "TRANSFER_IN_OTHER_TRANSFER_IN"),
        ]))
        self.assertEqual(analysis.spend_by_category, {})

    def test_refund_is_negative_spend_in_its_purchases_category(self):
        analysis = plaid_analysis(link_response([CARD], [
            side("Whole Foods", "acc-card", 60.00, "FOOD_AND_DRINK_GROCERIES", day=1),
            side("Whole Foods", "acc-card", -25.00, "FOOD_AND_DRINK_GROCERIES", day=4),
        ]))
        self.assertEqual(analysis.spend_by_category, {"Groceries": 35.00})
        self.assertEqual(analysis.spend_by_merchant, {"Whole Foods": 35.00})
        self.assertEqual(analysis.income, 0.00)

    def test_transfer_in_from_someone_else_is_income(self):
        analysis = plaid_analysis(link_response([CHECKING], [
            side("VENMO FROM JANE", "acc-checking", -40.00, "TRANSFER_IN_OTHER_TRANSFER_IN"),
        ]))
        self.assertEqual(analysis.spend_by_category, {})
        self.assertEqual(analysis.income, 40.00)

    def test_sandbox_fixture_links(self):
        # What Plaid returned for docs/plaid-testing.md Phase 1, checked against Step 4's Analysis
        platypus = link_response([CHECKING, SAVINGS, AUTO_LOAN], [
            side("ACME CORP PAYROLL", "acc-checking", -2500.00, "INCOME_SALARY", day=1),
            side("Trader Joe's", "acc-checking", 85.00, "FOOD_AND_DRINK_GROCERIES", day=1),
            side("PAYMENT TO FIRST GINGHAM CARD", "acc-checking", 500.00, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", day=2),
            side("DISCOVER E-PAYMENT", "acc-checking", 150.00, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", day=3),
            side("ONLINE TRANSFER FROM CHECKING XX1001", "acc-savings", -300.00, "TRANSFER_IN_ACCOUNT_TRANSFER", day=5),
            side("ONLINE TRANSFER TO SAVINGS XX2002", "acc-checking", 300.00, "TRANSFER_OUT_SAVINGS", day=5),
            side("ZELLE PAYMENT TO JOHN DOE", "acc-checking", 80.00, "TRANSFER_OUT_TRANSFER_OUT_FROM_APPS", day=7),
            side("PAYMENT RECEIVED THANK YOU", "acc-auto", -200.00, "TRANSFER_IN_OTHER_TRANSFER_IN", day=9),
            side("TOYOTA FINANCIAL PAYMENT", "acc-checking", 200.00, "LOAN_PAYMENTS_CAR_PAYMENT", day=9),
        ])
        gingham = link_response([CARD], [
            side("Whole Foods", "acc-card", 60.00, "FOOD_AND_DRINK_GROCERIES", day=1),
            side("Olive Garden", "acc-card", 40.00, "FOOD_AND_DRINK_RESTAURANT", day=2),
            side("PAYMENT THANK YOU", "acc-card", -500.00, "TRANSFER_IN_OTHER_TRANSFER_IN", day=2),
            side("Delta Air Lines", "acc-card", 400.00, "TRAVEL_FLIGHTS", day=3),
            side("Whole Foods", "acc-card", -25.00, "FOOD_AND_DRINK_GROCERIES", day=4),
            side("Starbucks", "acc-card", 5.00, "FOOD_AND_DRINK_COFFEE", day=6),
            side("ZQX HOLDINGS 4471", "acc-card", 12.00, "ENTERTAINMENT_VIDEO_GAMES", day=8),
        ], institution_name="First Gingham Credit Union")
        tartan = link_response([account(account_id="acc-tartan", type="depository", subtype="checking")], [
            side("Chipotle", "acc-tartan", 14.00, "FOOD_AND_DRINK_FAST_FOOD", day=6),
            side("Safeway", "acc-tartan", 52.00, "FOOD_AND_DRINK_GROCERIES", day=11),
        ], institution_name="Tartan Bank")

        analysis = plaid_analysis(platypus, gingham, tartan)

        self.assertEqual(analysis.spend_by_category, {
            "Travel": 400.00,
            "Mortgage & Loans": 200.00,
            "Groceries": 172.00,
            "Other": 92.00,
            "Restaurants & Fast Food": 59.00,
        })
        self.assertEqual(analysis.total_spend, 923.00)
        self.assertEqual(analysis.income, 2500.00)
        self.assertEqual(analysis.spend_by_merchant["Whole Foods"], 35.00)


def statement(*items):
    return AccountSummary(
        institution="Chase", account_name="Checking - 8821", period="Sep 01, 2026 - Sep 30, 2026", balance=1000.00,
        transactions=[
            LineItem(transaction_date="2026-09-02", description=desc, clean_merchant=desc, amount=amount, category=category)
            for desc, amount, category in items
        ],
    )


class StatementSpendTest(unittest.TestCase):
    # Statement amounts are positive for money out (the Gemini prompt) and nothing is paired,
    # so Spend follows Category alone

    def test_card_payments_and_transfers_are_not_spend_and_income_is_separate(self):
        analysis = analyze([statement(
            ("Trader Joe's", 85.00, "Groceries"),
            ("Whole Foods refund", -25.00, "Groceries"),
            ("Payment to Chase card", 500.00, "Payments & Credits"),
            ("ACME Corp payroll", -2500.00, "Income & Deposits"),
        )])
        self.assertEqual(analysis.spend_by_category, {"Groceries": 60.00})
        self.assertEqual(analysis.total_spend, 60.00)
        self.assertEqual(analysis.income, 2500.00)


if __name__ == "__main__":
    unittest.main()
