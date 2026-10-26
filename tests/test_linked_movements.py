import unittest
from datetime import date

from src.download_accounts import DownloadAccount
from src.downloads import Coverage, download
from src.linked_movements import pair_linked_movements
from src.plaid_transformer import summarize_link
from src.schemas import AccountSummary, SpendingCategory, Transaction
from tests.test_assisted_downloads import tartan
from tests.test_plaid_transformer import (
    EXPORTED_ON,
    WINDOW,
    account,
    link_response,
    transaction,
)


def pfc(detailed: str) -> dict:
    return {"primary": detailed.split("_")[0], "detailed": detailed}


CHECKING = account(account_id="acc-checking", name="Plaid Checking", mask="7829", type="depository", subtype="checking")
SAVINGS = account(account_id="acc-savings", name="Plaid Saving", mask="5745", type="depository", subtype="savings")
AUTO_LOAN = account(account_id="acc-auto", name="Plaid Auto", mask="2291", type="loan", subtype="auto")
CARD = account(account_id="acc-card", name="Plaid Credit Card", mask="9496", type="credit", subtype="credit card")


def side(name, account_id, amount, category, day=2):
    return transaction(name, account_id=account_id, amount=amount, date=date(2026, 10, day), personal_finance_category=pfc(category))


def downloaded(account: DownloadAccount, *transactions: Transaction) -> AccountSummary:
    """A Download Account's file over the whole window, as the Export takes it."""
    return download(account, Coverage(start=WINDOW.start, end=WINDOW.end), list(transactions), WINDOW).summary


def download_side(description, amount, category, day=2) -> Transaction:
    """A Download Transaction; amount is its Displayed amount."""
    return Transaction(
        transaction_date=date(2026, 10, day).isoformat(), description=description, clean_merchant=description,
        amount=amount, category=category,
    )


TARTAN_CARD = tartan("2222")
TARTAN_CHECKING = tartan("3333", kind="deposit")


def pairs(movements):
    return [(paid.description, received.description) for paid, received in movements.paired]


def flagged(movements):
    return [s.description for s in movements.unpaired]


def categories(*responses):
    movements = pair_linked_movements(list(responses))
    return {
        item.description: item.category.value
        for response in responses
        for summary in summarize_link(response, WINDOW, movements, EXPORTED_ON)
        for item in summary.transactions
    }


class PairingTest(unittest.TestCase):
    def test_card_payment_across_two_links_is_paired(self):
        movements = pair_linked_movements([
            link_response([CHECKING], [side("PAYMENT TO FIRST GINGHAM CARD", "acc-checking", 500.00, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")]),
            link_response([CARD], [side("PAYMENT THANK YOU", "acc-card", -500.00, "TRANSFER_IN_OTHER_TRANSFER_IN")], institution_name="First Gingham Credit Union"),
        ])
        self.assertEqual(pairs(movements), [("PAYMENT TO FIRST GINGHAM CARD", "PAYMENT THANK YOU")])
        self.assertEqual(flagged(movements), [])

    def test_card_payment_whose_card_side_is_a_loan_disbursement_is_paired(self):
        # Some Institutions file the card side of a Card Payment under LOAN_DISBURSEMENTS_ (#33)
        movements = pair_linked_movements([
            link_response([CHECKING, CARD], [
                side("PAYMENT TO CARD ENDING IN 9496", "acc-checking", 355.83, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
                side("PAYMENT THANK YOU - WEB", "acc-card", -355.83, "LOAN_DISBURSEMENTS_OTHER_DISBURSEMENT"),
            ], institution_name="First Gingham Credit Union"),
        ])
        self.assertEqual(pairs(movements), [("PAYMENT TO CARD ENDING IN 9496", "PAYMENT THANK YOU - WEB")])
        self.assertEqual(flagged(movements), [])

    def test_sides_five_days_apart_are_paired(self):
        # A weekend between the two posting dates can stretch a Card Payment to 5 days (#35)
        movements = pair_linked_movements([
            link_response([CHECKING, SAVINGS], [
                side("ONLINE TRANSFER TO SAVINGS", "acc-checking", 300.00, "TRANSFER_OUT_SAVINGS", day=1),
                side("ONLINE TRANSFER FROM CHECKING", "acc-savings", -300.00, "TRANSFER_IN_ACCOUNT_TRANSFER", day=6),
            ]),
        ])
        self.assertEqual(pairs(movements), [("ONLINE TRANSFER TO SAVINGS", "ONLINE TRANSFER FROM CHECKING")])
        self.assertEqual(flagged(movements), [])

    def test_sides_more_than_five_days_apart_are_not_paired(self):
        movements = pair_linked_movements([
            link_response([CHECKING, SAVINGS], [
                side("ONLINE TRANSFER TO SAVINGS", "acc-checking", 300.00, "TRANSFER_OUT_SAVINGS", day=1),
                side("ONLINE TRANSFER FROM CHECKING", "acc-savings", -300.00, "TRANSFER_IN_ACCOUNT_TRANSFER", day=7),
            ]),
        ])
        self.assertEqual(pairs(movements), [])
        self.assertEqual(flagged(movements), ["ONLINE TRANSFER TO SAVINGS", "ONLINE TRANSFER FROM CHECKING"])

    def test_the_closest_paying_side_wins_even_when_an_earlier_one_also_fits(self):
        # An earlier payment to an unlinked credit line must not take the card's receipt (#34)
        movements = pair_linked_movements([
            link_response([CHECKING], [
                side("CREDIT LINE REPAYMENT", "acc-checking", 150.00, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", day=1),
                side("CARD ACH PAYMENT", "acc-checking", 150.00, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", day=5),
            ]),
            link_response([CARD], [
                side("ONLINE PAYMENT - THANK YOU", "acc-card", -150.00, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", day=4),
            ], institution_name="First Gingham Credit Union"),
        ])
        self.assertEqual(pairs(movements), [("CARD ACH PAYMENT", "ONLINE PAYMENT - THANK YOU")])
        self.assertEqual(flagged(movements), ["CREDIT LINE REPAYMENT"])

    def test_a_refund_is_never_a_side(self):
        movements = pair_linked_movements([
            link_response([CHECKING, CARD], [
                side("ONLINE TRANSFER OUT", "acc-checking", 25.00, "TRANSFER_OUT_ACCOUNT_TRANSFER"),
                side("Pinecone Market", "acc-card", -25.00, "FOOD_AND_DRINK_GROCERIES"),
            ]),
        ])
        self.assertEqual(pairs(movements), [])

    def test_pending_activity_is_never_a_side(self):
        movements = pair_linked_movements([
            link_response([CHECKING, CARD], [
                side("PAYMENT TO CARD", "acc-checking", 500.00, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
                transaction("PAYMENT THANK YOU", account_id="acc-card", amount=-500.00, date=date(2026, 10, 2),
                            personal_finance_category=pfc("TRANSFER_IN_OTHER_TRANSFER_IN"), pending=True),
            ]),
        ])
        self.assertEqual(pairs(movements), [])
        self.assertEqual(flagged(movements), ["PAYMENT TO CARD"])

    def test_sandbox_fixture_links(self):
        # Shaped like what Plaid returned for docs/plaid-testing.md Phase 1 (seed_sandbox.py inspect), with neutral names
        platypus = link_response([CHECKING, SAVINGS, AUTO_LOAN], [
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
        gingham = link_response([CARD], [
            side("Pinecone Market", "acc-card", 60.00, "FOOD_AND_DRINK_GROCERIES", day=1),
            side("PAYMENT THANK YOU", "acc-card", -500.00, "TRANSFER_IN_OTHER_TRANSFER_IN", day=2),
            side("Pinecone Market", "acc-card", -25.00, "FOOD_AND_DRINK_GROCERIES", day=4),
            side("ZQX HOLDINGS 4471", "acc-card", 12.00, "ENTERTAINMENT_VIDEO_GAMES", day=8),
        ], institution_name="First Gingham Credit Union")
        tartan = link_response([account(account_id="acc-tartan", type="depository", subtype="checking")], [
            side("Burrito Barn", "acc-tartan", 14.00, "FOOD_AND_DRINK_FAST_FOOD", day=6),
            side("Juniper Foods", "acc-tartan", 52.00, "FOOD_AND_DRINK_GROCERIES", day=11),
        ], institution_name="Tartan Bank")

        movements = pair_linked_movements([platypus, gingham, tartan])

        self.assertEqual(pairs(movements), [
            ("PAYMENT TO FIRST GINGHAM CARD", "PAYMENT THANK YOU"),
            ("ONLINE TRANSFER TO SAVINGS XX2002", "ONLINE TRANSFER FROM CHECKING XX1001"),
            ("KESTREL AUTO FINANCE PAYMENT", "PAYMENT RECEIVED THANK YOU"),
        ])
        self.assertEqual(flagged(movements), ["WOMBAT CARD E-PAYMENT"])


class CrossSourcePairingTest(unittest.TestCase):
    def test_card_payment_from_a_plaid_account_to_a_download_account_is_paired(self):
        movements = pair_linked_movements(
            [link_response([CHECKING], [side("PAYMENT TO TARTAN CARD", "acc-checking", 500.00, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")])],
            [downloaded(TARTAN_CARD, download_side("PAYMENT RECEIVED", -500.00, SpendingCategory.PAYMENTS_CREDITS))],
        )
        self.assertEqual(pairs(movements), [("PAYMENT TO TARTAN CARD", "PAYMENT RECEIVED")])
        self.assertEqual(flagged(movements), [])
        received = movements.paired[0][1]
        self.assertEqual((received.institution, received.account_name), ("First Tartan Bank", "••2222"))

    def test_internal_transfer_from_a_download_account_to_a_plaid_account_is_paired(self):
        movements = pair_linked_movements(
            [link_response([SAVINGS], [side("ONLINE TRANSFER FROM TARTAN", "acc-savings", -300.00, "TRANSFER_IN_ACCOUNT_TRANSFER", day=3)])],
            [downloaded(TARTAN_CHECKING, download_side("TRANSFER TO SAVINGS", -300.00, SpendingCategory.PAYMENTS_CREDITS, day=1))],
        )
        self.assertEqual(pairs(movements), [("TRANSFER TO SAVINGS", "ONLINE TRANSFER FROM TARTAN")])
        self.assertEqual(flagged(movements), [])

    def test_loan_payment_from_a_download_account_to_a_plaid_loan_is_paired(self):
        movements = pair_linked_movements(
            [link_response([AUTO_LOAN], [side("PAYMENT RECEIVED THANK YOU", "acc-auto", -200.00, "TRANSFER_IN_OTHER_TRANSFER_IN")])],
            [downloaded(TARTAN_CHECKING, download_side("KESTREL AUTO FINANCE", -200.00, SpendingCategory.MORTGAGE_LOANS))],
        )
        self.assertEqual(pairs(movements), [("KESTREL AUTO FINANCE", "PAYMENT RECEIVED THANK YOU")])
        self.assertEqual(flagged(movements), [])

    def test_card_payment_between_two_download_accounts_is_paired(self):
        movements = pair_linked_movements([], [
            downloaded(TARTAN_CHECKING, download_side("PAYMENT TO CARD 2222", -75.00, SpendingCategory.PAYMENTS_CREDITS)),
            downloaded(TARTAN_CARD, download_side("PAYMENT RECEIVED", -75.00, SpendingCategory.PAYMENTS_CREDITS)),
        ])
        self.assertEqual(pairs(movements), [("PAYMENT TO CARD 2222", "PAYMENT RECEIVED")])

    def test_download_spending_is_never_a_side(self):
        movements = pair_linked_movements(
            [link_response([CHECKING], [side("ONLINE TRANSFER OUT", "acc-checking", 25.00, "TRANSFER_OUT_ACCOUNT_TRANSFER")])],
            [downloaded(TARTAN_CARD, download_side("Pinecone Market", -25.00, SpendingCategory.GROCERIES))],
        )
        self.assertEqual(pairs(movements), [])

    def test_an_unpaired_download_side_is_flagged(self):
        movements = pair_linked_movements([], [
            downloaded(TARTAN_CARD, download_side("PAYMENT RECEIVED", -500.00, SpendingCategory.PAYMENTS_CREDITS)),
            downloaded(TARTAN_CHECKING, download_side("KESTREL AUTO FINANCE", -200.00, SpendingCategory.MORTGAGE_LOANS)),
        ])
        self.assertEqual(flagged(movements), ["KESTREL AUTO FINANCE", "PAYMENT RECEIVED"])
        self.assertEqual([s.direction for s in movements.unpaired], ["paid", "received"])


class UnpairedFlaggingTest(unittest.TestCase):
    def test_card_payment_to_an_unlinked_card_is_flagged(self):
        movements = pair_linked_movements([
            link_response([CHECKING], [side("WOMBAT CARD E-PAYMENT", "acc-checking", 150.00, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")]),
        ])
        self.assertEqual(pairs(movements), [])
        self.assertEqual(flagged(movements), ["WOMBAT CARD E-PAYMENT"])
        self.assertEqual(movements.unpaired[0].direction, "paid")

    def test_transfer_out_to_someone_else_is_not_flagged(self):
        movements = pair_linked_movements([
            link_response([CHECKING], [side("PAYAPP PAYMENT TO JOHN DOE", "acc-checking", 80.00, "TRANSFER_OUT_TRANSFER_OUT_FROM_APPS")]),
        ])
        self.assertEqual(flagged(movements), [])

    def test_payment_into_a_liability_from_an_unlinked_account_is_flagged(self):
        movements = pair_linked_movements([
            link_response([CARD], [side("PAYMENT THANK YOU", "acc-card", -500.00, "TRANSFER_IN_OTHER_TRANSFER_IN")]),
        ])
        self.assertEqual(flagged(movements), ["PAYMENT THANK YOU"])
        self.assertEqual(movements.unpaired[0].direction, "received")

    def test_money_in_to_a_deposit_account_from_someone_else_is_not_flagged(self):
        movements = pair_linked_movements([
            link_response([CHECKING], [side("PAYAPP FROM JANE", "acc-checking", -40.00, "TRANSFER_IN_OTHER_TRANSFER_IN")]),
        ])
        self.assertEqual(flagged(movements), [])


class AccountKindTest(unittest.TestCase):
    def test_an_investment_account_is_a_deposit_account_in_the_export_and_in_linked_movements(self):
        brokerage = account(account_id="acc-brokerage", name="Plaid Brokerage", mask="3333", type="investment", subtype="brokerage")
        response = link_response([brokerage], [side("TRANSFER FROM JANE", "acc-brokerage", -40.00, "TRANSFER_IN_OTHER_TRANSFER_IN")])
        movements = pair_linked_movements([response])
        summary = summarize_link(response, WINDOW, movements, EXPORTED_ON)[0]
        self.assertEqual(flagged(movements), [])
        self.assertEqual((summary.account_kind, summary.transactions[0].amount), ("deposit", 40.00))


class SideCategoryTest(unittest.TestCase):
    def test_both_sides_of_a_card_payment_are_payments_and_credits(self):
        self.assertEqual(categories(link_response([CHECKING, CARD], [
            side("PAYMENT TO FIRST GINGHAM CARD", "acc-checking", 500.00, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
            side("PAYMENT THANK YOU", "acc-card", -500.00, "TRANSFER_IN_OTHER_TRANSFER_IN"),
        ])), {"PAYMENT TO FIRST GINGHAM CARD": "Payments & Credits", "PAYMENT THANK YOU": "Payments & Credits"})

    def test_a_loan_payments_paying_side_is_mortgage_and_loans_and_the_loans_side_is_payments_and_credits(self):
        self.assertEqual(categories(link_response([CHECKING, AUTO_LOAN], [
            side("KESTREL AUTO FINANCE PAYMENT", "acc-checking", 200.00, "LOAN_PAYMENTS_CAR_PAYMENT"),
            side("PAYMENT RECEIVED THANK YOU", "acc-auto", -200.00, "TRANSFER_IN_OTHER_TRANSFER_IN"),
        ])), {"KESTREL AUTO FINANCE PAYMENT": "Mortgage & Loans", "PAYMENT RECEIVED THANK YOU": "Payments & Credits"})

    def test_an_unpaired_payment_into_a_liability_is_payments_and_credits(self):
        self.assertEqual(categories(link_response([CARD], [
            side("PAYMENT THANK YOU", "acc-card", -500.00, "TRANSFER_IN_OTHER_TRANSFER_IN"),
        ])), {"PAYMENT THANK YOU": "Payments & Credits"})


if __name__ == "__main__":
    unittest.main()
