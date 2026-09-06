from typing import List
from src.schemas import PlaidTransaction, PlaidAccount, StatementSummary, LineItem, SpendingCategory

# A mapping from Plaid's detailed categories to our internal SpendingCategory enum.
# This is not exhaustive and can be expanded.
PLAID_CATEGORY_MAPPING = {
    "FOOD_AND_DRINK_RESTAURANTS": SpendingCategory.RESTAURANTS_FAST_FOOD,
    "FOOD_AND_DRINK_GROCERIES": SpendingCategory.GROCERIES,
    "TRANSPORTATION_FUEL": SpendingCategory.CAR_FUEL,
    "TRANSPORTATION_MAINTENANCE": SpendingCategory.CAR_MAINTENANCE,
    "UTILITIES": SpendingCategory.UTILITIES_SUBSCRIPTIONS,
    "GENERAL_SERVICES_INSURANCE": SpendingCategory.UTILITIES_SUBSCRIPTIONS,
    "TRANSFER_IN_DEPOSIT": SpendingCategory.INCOME_DEPOSITS,
    "TRANSFER_OUT_ACCOUNT_TRANSFER": SpendingCategory.PAYMENTS_CREDITS,
    "TRANSFER_OUT_PAYMENT": SpendingCategory.PAYMENTS_CREDENTS,
}

def transform_plaid_transactions(transactions: List[PlaidTransaction], account: PlaidAccount, start_date: str, end_date: str) -> StatementSummary:
    """
    Transforms a list of Plaid transactions for a given account into a StatementSummary.
    """
    line_items = []
    for tx in transactions:
        # Plaid amounts are positive for debits, negative for credits.
        # Our LineItem model expects positive for spend/debits, negative for credits/payments
        amount = tx.amount if tx.amount < 0 else tx.amount

        # Try to map the Plaid category to our enum
        category = SpendingCategory.OTHER
        if tx.category:
            for cat_part in tx.category:
                # Using personal_finance_category would be more robust
                if cat_part.upper() in PLAID_CATEGORY_MAPPING:
                    category = PLAID_CATEGORY_MAPPING[cat_part.upper()]
                    break
        
        line_items.append(
            LineItem(
                transaction_date=tx.date,
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
