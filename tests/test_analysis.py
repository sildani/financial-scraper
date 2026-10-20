import unittest

from src.analysis import analyze
from src.linked_movements import pair_linked_movements
from src.plaid_transformer import summarize_link
from tests.test_linked_movements import CHECKING, SAVINGS, AUTO_LOAN, CARD, side
from src.schemas import AccountSummary, ParsedTransaction, Transaction
from src.statements import statement_summary
from tests.test_plaid_transformer import WINDOW, account, link_response


def plaid_analysis(*responses):
    movements = pair_linked_movements(list(responses))
    summaries = [
        summary
        for response in responses
        for summary in summarize_link(response, WINDOW, movements)
    ]
    return analyze(summaries)


class PlaidSpendTest(unittest.TestCase):
    def test_purchases_are_positive_spend_on_every_account_kind(self):
        analysis = plaid_analysis(link_response([CHECKING, CARD], [
            side("Thistle Grocery", "acc-checking", 85.00, "FOOD_AND_DRINK_GROCERIES"),
            side("Pinecone Market", "acc-card", 60.00, "FOOD_AND_DRINK_GROCERIES"),
        ]))
        self.assertEqual(analysis.spend_by_category, {"Groceries": 145.00})
        self.assertEqual(analysis.total_spend, 145.00)

    def test_income_is_reported_separately_and_never_netted(self):
        analysis = plaid_analysis(link_response([CHECKING], [
            side("ACME CORP PAYROLL", "acc-checking", -2500.00, "INCOME_SALARY"),
            side("Thistle Grocery", "acc-checking", 85.00, "FOOD_AND_DRINK_GROCERIES"),
        ]))
        self.assertEqual(analysis.spend_by_category, {"Groceries": 85.00})
        self.assertEqual(analysis.total_spend, 85.00)
        self.assertEqual(analysis.income, 2500.00)

    def test_card_payments_and_internal_transfers_are_not_spend(self):
        analysis = plaid_analysis(link_response([CHECKING, SAVINGS, CARD], [
            side("PAYMENT TO FIRST GINGHAM CARD", "acc-checking", 500.00, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
            side("PAYMENT THANK YOU", "acc-card", -500.00, "TRANSFER_IN_OTHER_TRANSFER_IN"),
            side("WOMBAT CARD E-PAYMENT", "acc-checking", 150.00, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
            side("ONLINE TRANSFER TO SAVINGS", "acc-checking", 300.00, "TRANSFER_OUT_SAVINGS"),
            side("ONLINE TRANSFER FROM CHECKING", "acc-savings", -300.00, "TRANSFER_IN_ACCOUNT_TRANSFER"),
        ]))
        self.assertEqual(analysis.spend_by_category, {})
        self.assertEqual((analysis.total_spend, analysis.income), (0.00, 0.00))

    def test_loan_payment_is_spend_on_the_paying_side_only(self):
        analysis = plaid_analysis(link_response([CHECKING, AUTO_LOAN], [
            side("KESTREL AUTO FINANCE PAYMENT", "acc-checking", 200.00, "LOAN_PAYMENTS_CAR_PAYMENT"),
            side("PAYMENT RECEIVED THANK YOU", "acc-auto", -200.00, "TRANSFER_IN_OTHER_TRANSFER_IN"),
        ]))
        self.assertEqual(analysis.spend_by_category, {"Mortgage & Loans": 200.00})
        self.assertEqual(analysis.total_spend, 200.00)

    def test_loan_payment_to_an_unlinked_lender_is_still_spend(self):
        analysis = plaid_analysis(link_response([CHECKING], [
            side("QUOKKA HOME LOANS", "acc-checking", 1800.00, "LOAN_PAYMENTS_MORTGAGE_PAYMENT"),
        ]))
        self.assertEqual(analysis.spend_by_category, {"Mortgage & Loans": 1800.00})

    def test_payment_into_a_liability_from_an_unlinked_account_is_not_spend(self):
        analysis = plaid_analysis(link_response([CARD], [
            side("PAYMENT THANK YOU", "acc-card", -500.00, "TRANSFER_IN_OTHER_TRANSFER_IN"),
        ]))
        self.assertEqual(analysis.spend_by_category, {})

    def test_refund_is_negative_spend_in_its_purchases_category(self):
        analysis = plaid_analysis(link_response([CARD], [
            side("Pinecone Market", "acc-card", 60.00, "FOOD_AND_DRINK_GROCERIES", day=1),
            side("Pinecone Market", "acc-card", -25.00, "FOOD_AND_DRINK_GROCERIES", day=4),
        ]))
        self.assertEqual(analysis.spend_by_category, {"Groceries": 35.00})
        self.assertEqual(analysis.spend_by_merchant, {"Pinecone Market": 35.00})
        self.assertEqual(analysis.income, 0.00)

    def test_transfer_in_from_someone_else_is_income(self):
        analysis = plaid_analysis(link_response([CHECKING], [
            side("PAYAPP FROM JANE", "acc-checking", -40.00, "TRANSFER_IN_OTHER_TRANSFER_IN"),
        ]))
        self.assertEqual(analysis.spend_by_category, {})
        self.assertEqual(analysis.income, 40.00)

    def test_sandbox_fixture_links(self):
        # Shaped like what Plaid returned for docs/plaid-testing.md Phase 1, with neutral names, checked against Step 4's Analysis
        analysis = plaid_analysis(sandbox_platypus(), sandbox_gingham(), sandbox_tartan())

        self.assertEqual(analysis.spend_by_category, {
            "Travel": 400.00,
            "Mortgage & Loans": 200.00,
            "Groceries": 172.00,
            "Other": 92.00,
            "Restaurants & Fast Food": 59.00,
        })
        self.assertEqual(analysis.total_spend, 923.00)
        self.assertEqual(analysis.income, 2500.00)
        self.assertEqual(analysis.spend_by_merchant["Pinecone Market"], 35.00)

    def test_sandbox_fixture_links_without_tartan(self):
        # docs/plaid-testing.md Phase 1 Step 5: Tartan needs re-linking, so the Partial Export drops it
        analysis = plaid_analysis(sandbox_platypus(), sandbox_gingham())

        self.assertEqual(analysis.spend_by_category, {
            "Travel": 400.00,
            "Mortgage & Loans": 200.00,
            "Groceries": 120.00,
            "Other": 92.00,
            "Restaurants & Fast Food": 45.00,
        })
        self.assertEqual(analysis.total_spend, 857.00)


def sandbox_platypus():
    return link_response([CHECKING, SAVINGS, AUTO_LOAN], [
        side("ACME CORP PAYROLL", "acc-checking", -2500.00, "INCOME_SALARY", day=1),
        side("Thistle Grocery", "acc-checking", 85.00, "FOOD_AND_DRINK_GROCERIES", day=1),
        side("PAYMENT TO FIRST GINGHAM CARD", "acc-checking", 500.00, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", day=2),
        side("WOMBAT CARD E-PAYMENT", "acc-checking", 150.00, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", day=3),
        side("ONLINE TRANSFER FROM CHECKING XX1001", "acc-savings", -300.00, "TRANSFER_IN_ACCOUNT_TRANSFER", day=5),
        side("ONLINE TRANSFER TO SAVINGS XX2002", "acc-checking", 300.00, "TRANSFER_OUT_SAVINGS", day=5),
        side("PAYAPP PAYMENT TO JOHN DOE", "acc-checking", 80.00, "TRANSFER_OUT_TRANSFER_OUT_FROM_APPS", day=7),
        side("PAYMENT RECEIVED THANK YOU", "acc-auto", -200.00, "TRANSFER_IN_OTHER_TRANSFER_IN", day=9),
        side("KESTREL AUTO FINANCE PAYMENT", "acc-checking", 200.00, "LOAN_PAYMENTS_CAR_PAYMENT", day=9),
    ])


def sandbox_gingham():
    return link_response([CARD], [
        side("Pinecone Market", "acc-card", 60.00, "FOOD_AND_DRINK_GROCERIES", day=1),
        side("Noodle Hall", "acc-card", 40.00, "FOOD_AND_DRINK_RESTAURANT", day=2),
        side("PAYMENT THANK YOU", "acc-card", -500.00, "TRANSFER_IN_OTHER_TRANSFER_IN", day=2),
        side("Heron Airways", "acc-card", 400.00, "TRAVEL_FLIGHTS", day=3),
        side("Pinecone Market", "acc-card", -25.00, "FOOD_AND_DRINK_GROCERIES", day=4),
        side("Bean Counter Coffee", "acc-card", 5.00, "FOOD_AND_DRINK_COFFEE", day=6),
        side("ZQX HOLDINGS 4471", "acc-card", 12.00, "ENTERTAINMENT_VIDEO_GAMES", day=8),
        ], institution_name="First Gingham Credit Union")


def sandbox_tartan():
    return link_response([account(account_id="acc-tartan", type="depository", subtype="checking")], [
        side("Burrito Barn", "acc-tartan", 14.00, "FOOD_AND_DRINK_FAST_FOOD", day=6),
        side("Juniper Foods", "acc-tartan", 52.00, "FOOD_AND_DRINK_GROCERIES", day=11),
    ], institution_name="Tartan Bank")


def statement(account_kind, *items):
    """A Statement as Gemini parses it: amounts are positive for money out on every Account kind."""
    return statement_summary(AccountSummary(
        institution="First Gingham Credit Union", account_name="Checking - 0000", period="Sep 01, 2026 - Sep 30, 2026",
        balance=1000.00, account_kind=account_kind,
        transactions=[
            ParsedTransaction(transaction_date="2026-09-02", description=desc, clean_merchant=desc, amount=amount, category=category)
            for desc, amount, category in items
        ],
    ))


class CachedStatementTest(unittest.TestCase):
    def test_a_parse_cached_before_the_transaction_rename_still_loads(self):
        # A cached Gemini parse, as model_dump_json wrote it before the models were renamed
        cached = {
            "institution": "First Gingham Credit Union", "account_name": "Checking - 0000",
            "period": "Sep 01, 2026 - Sep 30, 2026", "account_kind": "deposit", "balance": 1000.0,
            "amount_due": None, "due_date": None, "interest_charged": 0.0,
            "transactions": [
                {"transaction_date": "2026-09-02", "description": "GINGHAM GROCERS #123", "clean_merchant": "Gingham Grocers", "amount": 85.0, "category": "Groceries"},
                {"transaction_date": "2026-09-15", "description": "ACME CORP PAYROLL", "clean_merchant": "ACME Corp", "amount": -2500.0, "category": "Income & Deposits"},
            ],
        }
        summary = statement_summary(AccountSummary.model_validate(cached))
        self.assertTrue(all(isinstance(tx, Transaction) for tx in summary.transactions))
        self.assertEqual([(tx.amount, tx.spend(), tx.income()) for tx in summary.transactions], [(-85.0, 85.0, None), (2500.0, None, 2500.0)])


class StatementSpendTest(unittest.TestCase):
    # Nothing is paired on a Statement, so Spend follows Category alone

    def test_deposit_account_shows_money_in_as_positive_and_spend_is_unchanged(self):
        summary = statement("deposit",
            ("Thistle Grocery", 85.00, "Groceries"),
            ("Pinecone Market refund", -25.00, "Groceries"),
            ("Payment to card", 500.00, "Payments & Credits"),
            ("ACME Corp payroll", -2500.00, "Income & Deposits"),
        )
        self.assertEqual([tx.amount for tx in summary.transactions], [-85.00, 25.00, -500.00, 2500.00])
        analysis = analyze([summary])
        self.assertEqual(analysis.spend_by_category, {"Groceries": 60.00})
        self.assertEqual(analysis.total_spend, 60.00)
        self.assertEqual(analysis.income, 2500.00)

    def test_liability_amounts_are_unchanged(self):
        summary = statement("liability",
            ("Pinecone Market", 60.00, "Groceries"),
            ("Payment thank you", -500.00, "Payments & Credits"),
        )
        self.assertEqual([tx.amount for tx in summary.transactions], [60.00, -500.00])
        self.assertEqual(analyze([summary]).spend_by_category, {"Groceries": 60.00})

    def test_a_loan_statements_payment_received_is_not_spend(self):
        summary = statement("liability",
            ("Payment received thank you", -200.00, "Payments & Credits"),
            ("Late fee", 25.00, "Mortgage & Loans"),
        )
        analysis = analyze([summary])
        self.assertEqual(analysis.spend_by_category, {"Mortgage & Loans": 25.00})
        self.assertEqual((analysis.total_spend, analysis.income), (25.00, 0.00))


if __name__ == "__main__":
    unittest.main()
