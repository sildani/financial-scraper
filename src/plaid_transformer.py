from typing import List
from src.schemas import PlaidTransaction, PlaidAccount, AccountSummary, LineItem, SpendingCategory, PlaidPersonalFinanceCategory

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

# Fallback mapping for when Plaid's category is missing or unhelpful.
# Matches keywords in the merchant name (case-insensitive).
MERCHANT_KEYWORD_MAPPING = {
    "DELTA": SpendingCategory.TRAVEL,
    "AMERICAN AIRLINES": SpendingCategory.TRAVEL,
    "UNITED AIRLINES": SpendingCategory.TRAVEL,
    "STARBUCKS": SpendingCategory.RESTAURANTS_FAST_FOOD,
    "MCDONALD'S": SpendingCategory.RESTAURANTS_FAST_FOOD,
}

def summarize_link(response: dict, start_date: str, end_date: str) -> List[AccountSummary]:
    """
    Summarizes every Account in one Link's /transactions/get response, including Accounts
    with no Transactions in the Export window, since every Account's Balance belongs in the Export.
    """
    # Plaid documents institution_name as nullable, so fall back to the Institution's ID
    item = response["item"]
    institution = item.get("institution_name") or item.get("institution_id") or "Unknown Institution"
    accounts = [PlaidAccount(**acc) for acc in response["accounts"]]
    transactions = [PlaidTransaction(**tx) for tx in response.get("transactions", [])]
    return [
        transform_plaid_transactions(
            [tx for tx in transactions if tx.account_id == account.account_id], account, start_date, end_date, institution
        )
        for account in accounts
    ]

def transform_plaid_transactions(transactions: List[PlaidTransaction], account: PlaidAccount, start_date: str, end_date: str, institution: str) -> AccountSummary:
    """
    Transforms a list of Plaid transactions for a given account into an AccountSummary.
    """
    line_items = []
    for tx in transactions:
        # Pending activity is not a Transaction (GLOSSARY.md); only posted ones enter the record
        if tx.pending:
            continue

        # Plaid amounts are positive for money out on every Account kind. The Displayed amount
        # follows the Account's kind (ADR-0001), so a Deposit account shows money in as positive.
        amount = -tx.amount if account.type == "depository" else tx.amount
        
        # Try to map the Plaid category to our enum
        category = SpendingCategory.OTHER
        categorized = False
        if tx.personal_finance_category:
            # Find the best (longest) matching category prefix
            best_match_key = None
            for plaid_category, mapped_category in PLAID_CATEGORY_MAPPING.items():
                if tx.personal_finance_category.detailed.startswith(plaid_category):
                    if best_match_key is None or len(plaid_category) > len(best_match_key):
                        best_match_key = plaid_category
            
            if best_match_key:
                category = PLAID_CATEGORY_MAPPING[best_match_key]
                categorized = True
        
        # If Plaid category didn't yield a result, try matching merchant name
        if not categorized and tx.merchant_name:
            for keyword, mapped_category in MERCHANT_KEYWORD_MAPPING.items():
                if keyword in tx.merchant_name.upper():
                    category = mapped_category
                    categorized = True
                    break

        line_items.append(
            LineItem(
                transaction_date=tx.date.isoformat(),
                description=tx.name,
                clean_merchant=tx.merchant_name or tx.name,
                amount=amount,
                category=category,
            )
        )

    return AccountSummary(
        institution=institution,
        account_name=f"{account.name} ({account.mask})",
        period=f"{start_date} to {end_date}",
        balance=account.balances.current,
        transactions=line_items,
    )
