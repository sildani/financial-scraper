from typing import List
from src.schemas import PlaidTransaction, PlaidAccount, StatementSummary, LineItem, SpendingCategory, PlaidPersonalFinanceCategory

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

def transform_plaid_transactions(transactions: List[PlaidTransaction], account: PlaidAccount, start_date: str, end_date: str) -> StatementSummary:
    """
    Transforms a list of Plaid transactions for a given account into a StatementSummary.
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

    return StatementSummary(
        card_issuer=account.name,
        account_name=f"{account.name} ({account.mask})",
        statement_period=f"{start_date} to {end_date}",
        total_balance_due=account.balances.current,
        transactions=line_items,
    )
