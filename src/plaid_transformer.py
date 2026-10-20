from typing import AbstractSet, List

from src.export_window import ExportWindow
from src.linked_movements import LinkedMovements
from src.schemas import (
    AccountSummary,
    PlaidAccount,
    PlaidTransaction,
    SpendingCategory,
    Transaction,
    category_spend_and_income,
    link_institution,
)

# A mapping from Plaid's personal finance categories to our fixed Categories, keyed by
# PFCv2 detailed code or a prefix of one (the longest matching key wins). Taxonomy:
# https://plaid.com/documents/pfc-taxonomy-all.csv
# Anything that doesn't clearly fit a Category is left unmapped and goes to Other.
PLAID_CATEGORY_MAPPING = {
    # --- Transfers, Payments, and Income ---
    "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT": SpendingCategory.PAYMENTS_CREDITS,
    "TRANSFER_IN_ACCOUNT_TRANSFER": SpendingCategory.PAYMENTS_CREDITS,
    "TRANSFER_OUT_ACCOUNT_TRANSFER": SpendingCategory.PAYMENTS_CREDITS,
    "TRANSFER_IN_SAVINGS": SpendingCategory.PAYMENTS_CREDITS,
    "TRANSFER_OUT_SAVINGS": SpendingCategory.PAYMENTS_CREDITS,
    "TRANSFER_IN_DEPOSIT": SpendingCategory.INCOME_DEPOSITS,
    "INCOME": SpendingCategory.INCOME_DEPOSITS,  # Also covers PFCv1 names such as INCOME_WAGES

    # --- Spending Categories ---
    # Food and Drink
    "FOOD_AND_DRINK_RESTAURANT": SpendingCategory.RESTAURANTS_FAST_FOOD,
    "FOOD_AND_DRINK_FAST_FOOD": SpendingCategory.RESTAURANTS_FAST_FOOD,
    "FOOD_AND_DRINK_COFFEE": SpendingCategory.RESTAURANTS_FAST_FOOD,
    "FOOD_AND_DRINK_GROCERIES": SpendingCategory.GROCERIES,

    # Travel and Car
    "TRAVEL": SpendingCategory.TRAVEL,
    "TRANSPORTATION_GAS": SpendingCategory.CAR_FUEL,
    "GENERAL_SERVICES_AUTOMOTIVE": SpendingCategory.CAR_MAINTENANCE,

    # Utilities, Services & Subscriptions
    "RENT_AND_UTILITIES": SpendingCategory.UTILITIES_SUBSCRIPTIONS,
    "RENT_AND_UTILITIES_RENT": SpendingCategory.OTHER,
    "GENERAL_SERVICES_INSURANCE": SpendingCategory.UTILITIES_SUBSCRIPTIONS,

    # Loans
    "LOAN_PAYMENTS_MORTGAGE_PAYMENT": SpendingCategory.MORTGAGE_LOANS,
    "LOAN_PAYMENTS_STUDENT_LOAN_PAYMENT": SpendingCategory.MORTGAGE_LOANS,
    "LOAN_PAYMENTS_CAR_PAYMENT": SpendingCategory.MORTGAGE_LOANS,
    "LOAN_PAYMENTS_PERSONAL_LOAN_PAYMENT": SpendingCategory.MORTGAGE_LOANS,
}

def summarize_link(response: dict, window: ExportWindow, movements: LinkedMovements) -> List[AccountSummary]:
    """
    Summarizes every Account in one Link's /transactions/get response, including Accounts
    with no Transactions in the Export window, since every Account's Balance belongs in the Export.
    """
    institution = link_institution(response["item"])
    accounts = [PlaidAccount(**acc) for acc in response["accounts"]]
    transactions = [PlaidTransaction(**tx) for tx in response.get("transactions", [])]
    movement_ids = {side.transaction_id for pair in movements.paired for side in pair}
    movement_ids |= {side.transaction_id for side in movements.unpaired}
    return [
        transform_plaid_transactions(
            [tx for tx in transactions if tx.account_id == account.account_id], account, window, institution, movement_ids
        )
        for account in accounts
    ]

def transform_plaid_transactions(transactions: List[PlaidTransaction], account: PlaidAccount, window: ExportWindow, institution: str, movement_ids: AbstractSet[str] = frozenset()) -> AccountSummary:
    """
    Transforms a list of Plaid transactions for a given account into an AccountSummary.
    movement_ids are the transaction IDs of every Linked Movement side, paired or not.
    """
    posted = []
    for tx in transactions:
        # Pending activity is not a Transaction (GLOSSARY.md); only posted ones enter the record
        if tx.pending:
            continue

        # Plaid amounts are positive for money out on every Account kind. The Displayed amount
        # follows the Account's kind (ADR-0001), so a Deposit account shows money in as positive.
        amount = -tx.amount if account.kind == "deposit" else tx.amount
        
        # Try to map the Plaid category to our enum
        category = SpendingCategory.OTHER
        if tx.personal_finance_category:
            # Find the best (longest) matching category prefix
            best_match_key = None
            for plaid_category in PLAID_CATEGORY_MAPPING:
                if tx.personal_finance_category.detailed.startswith(plaid_category):
                    if best_match_key is None or len(plaid_category) > len(best_match_key):
                        best_match_key = plaid_category
            
            if best_match_key:
                category = PLAID_CATEGORY_MAPPING[best_match_key]

        # Spend and Income are money-out and money-in values, so they use Plaid's own sign.
        # Plaid Accounts re-decide only what Category alone would call Spend.
        by_category = category_spend_and_income(category, tx.amount)
        spend_amount, income_amount = by_category
        if by_category.spend is not None:
            if tx.transaction_id in movement_ids and not is_loan_payment(tx, category):
                # The other side of a Card Payment or loan payment, e.g. TRANSFER_IN_OTHER_TRANSFER_IN on the card (#32)
                category, spend_amount = SpendingCategory.PAYMENTS_CREDITS, None
            elif is_transfer_in(tx):
                spend_amount, income_amount = None, -tx.amount  # Money in from someone else, e.g. a payment app transfer from a friend

        posted.append(
            Transaction(
                transaction_date=tx.date.isoformat(),
                description=tx.name,
                clean_merchant=tx.merchant_name or tx.name,
                amount=amount,
                category=category,
                spend_amount=spend_amount,
                income_amount=income_amount,
            )
        )

    return AccountSummary(
        institution=institution,
        account_name=account.label,
        period=f"{window.start.isoformat()} to {window.end.isoformat()}",
        account_kind=account.kind,
        balance=account.balances.current,
        transactions=posted,
    )


def is_loan_payment(tx: PlaidTransaction, category: SpendingCategory) -> bool:
    """The paying side of a loan payment, which is Spend; the loan's own side is only its counterpart."""
    return category == SpendingCategory.MORTGAGE_LOANS and tx.amount > 0


def is_transfer_in(tx: PlaidTransaction) -> bool:
    return bool(tx.personal_finance_category) and tx.personal_finance_category.detailed.startswith("TRANSFER_IN_")
