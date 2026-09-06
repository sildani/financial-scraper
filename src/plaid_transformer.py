from typing import List
from src.schemas import PlaidTransaction, PlaidAccount, StatementSummary, LineItem, SpendingCategory, PlaidPersonalFinanceCategory

# A mapping from Plaid's detailed categories to our internal SpendingCategory enum.
# This is not exhaustive and can be expanded.
PLAID_CATEGORY_MAPPING = {
    # --- Transfers, Payments, and Income (Most specific rules first) ---
    "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT": SpendingCategory.PAYMENTS_CREDITS,
    "TRANSFER_IN_ACCOUNT_TRANSFER": SpendingCategory.PAYMENTS_CREDITS,
    "TRANSFER_OUT_ACCOUNT_TRANSFER": SpendingCategory.PAYMENTS_CREDITS,
    "TRANSFER_OUT_PAYMENT": SpendingCategory.PAYMENTS_CREDITS,
    "TRANSFER_IN_DEPOSIT": SpendingCategory.INCOME_DEPOSITS,
    "TRANSFER_IN_WAGES": SpendingCategory.INCOME_DEPOSITS,

    # --- Spending Categories ---
    # Food and Drink
    "FOOD_AND_DRINK_RESTAURANTS": SpendingCategory.RESTAURANTS_FAST_FOOD,
    "FOOD_AND_DRINK_FAST_FOOD": SpendingCategory.RESTAURANTS_FAST_FOOD,
    "FOOD_AND_DRINK_COFFEE_SHOP": SpendingCategory.RESTAURANTS_FAST_FOOD,
    "FOOD_AND_DRINK_GROCERIES": SpendingCategory.GROCERIES,
    
    # Transportation (Airlines are specific)
    "TRANSPORTATION_AIRLINES_AND_AVIATION_SERVICES": SpendingCategory.TRAVEL,
    "TRANSPORTATION_FUEL": SpendingCategory.CAR_FUEL,
    "TRANSPORTATION_PUBLIC_TRANSIT": SpendingCategory.CAR_MAINTENANCE,
    "TRANSPORTATION_TAXIS_AND_RIDE_SHARES": SpendingCategory.CAR_MAINTENANCE,
    "GENERAL_MERCHANDISE_AUTO_PARTS": SpendingCategory.CAR_MAINTENANCE,
    
    # General Merchandise & Shopping
    "GENERAL_MERCHANDISE_SUPERSTORES": SpendingCategory.GROCERIES, # e.g., Target, Walmart
    "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES": SpendingCategory.UTILITIES_SUBSCRIPTIONS, # e.g., Amazon
    "GENERAL_MERCHANDISE_DEPARTMENT_STORES": SpendingCategory.UTILITIES_SUBSCRIPTIONS,
    "GENERAL_MERCHANDISE_CLOTHING_AND_ACCESSORIES": SpendingCategory.UTILITIES_SUBSCRIPTIONS,
    
    # Utilities, Services & Subscriptions
    "UTILITIES": SpendingCategory.UTILITIES_SUBSCRIPTIONS,
    "GENERAL_SERVICES_INSURANCE": SpendingCategory.UTILITIES_SUBSCRIPTIONS,
    "GENERAL_SERVICES_TELECOMMUNICATION_SERVICES": SpendingCategory.UTILITIES_SUBSCRIPTIONS,
    "ENTERTAINMENT_STREAMING_SERVICES": SpendingCategory.UTILITIES_SUBSCRIPTIONS,
    
    # Loans (Broad rules last)
    "LOAN_PAYMENTS_MORTGAGE_PAYMENT": SpendingCategory.MORTGAGE_LOANS,
    "LOAN_PAYMENTS_STUDENT_LOAN_PAYMENT": SpendingCategory.MORTGAGE_LOANS,
    "LOAN_PAYMENTS_AUTO_LOAN_PAYMENT": SpendingCategory.MORTGAGE_LOANS,
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
        # Plaid amounts are positive for debits, negative for credits.
        # Our LineItem model expects the same, so we don't need to change the sign for spending.
        # For credits/payments (which are negative in Plaid), we keep them negative.
        amount = tx.amount
        
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
