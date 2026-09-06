from decimal import Decimal
from enum import Enum
from typing import Optional, List
from datetime import date
from pydantic import BaseModel, Field


class SpendingCategory(str, Enum):
    GROCERIES = "Groceries"
    RESTAURANTS_FAST_FOOD = "Restaurants & Fast Food"
    CAR_FUEL = "Car Fuel"
    CAR_MAINTENANCE = "Car Maintenance"
    TRAVEL = "Travel"
    UTILITIES_SUBSCRIPTIONS = "Utilities & Subscriptions"
    PAYMENTS_CREDITS = "Payments & Credits"
    INCOME_DEPOSITS = "Income & Deposits"
    MORTGAGE_LOANS = "Mortgage & Loans"
    OTHER = "Other"


class LineItem(BaseModel):
    transaction_date: str = Field(description="Transaction date in YYYY-MM-DD format")
    description: str = Field(description="Raw text descriptor from statement")
    clean_merchant: str = Field(description="Normalized vendor or entity name")
    amount: float = Field(description="Positive decimal value for spend/debits, negative for credits/payments/deposits")
    category: SpendingCategory = Field(description="Categorization based on vendor or transaction type")


class StatementSummary(BaseModel):
    card_issuer: str = Field(description="Name of the financial institution, e.g., Amex, Citi, Chase, Bank of America, Goldman Sachs")
    account_name: str = Field(description="Account descriptor or last 4 digits (e.g., 'Amex Gold - 1004', 'Checking - 8821')")
    statement_period: str = Field(description="Statement period string, e.g., 'Aug 01, 2026 - Aug 31, 2026'")
    total_balance_due: float = Field(description="New balance, ending balance, or total amount due")
    payment_due_date: Optional[str] = Field(default=None, description="Due date in YYYY-MM-DD format, if applicable")
    interest_charged: Optional[float] = Field(default=0.0, description="Total interest or fees charged, if applicable")
    transactions: List[LineItem] = Field(description="List of all transactions found in the statement")
# --- Plaid Models ---

class PlaidAccountBalances(BaseModel):
    available: Optional[float] = None
    current: float
    limit: Optional[float] = None
    iso_currency_code: str
    unofficial_currency_code: Optional[str] = None

class PlaidAccount(BaseModel):
    account_id: str
    balances: PlaidAccountBalances
    mask: str
    name: str
    official_name: Optional[str] = None
    type: str
    subtype: str

class PlaidTransaction(BaseModel):
    account_id: str
    amount: float
    iso_currency_code: str
    category: Optional[List[str]] = None
    category_id: Optional[str] = None
    date: date
    name: str
    merchant_name: Optional[str] = None
    pending: bool
    transaction_id: str
    personal_finance_category: Optional['PlaidPersonalFinanceCategory'] = None
    
class PlaidPersonalFinanceCategory(BaseModel):
    primary: str
    detailed: str

class PlaidTransactionsResponse(BaseModel):
    accounts: List[PlaidAccount]
    transactions: List[PlaidTransaction]
    total_transactions: int
