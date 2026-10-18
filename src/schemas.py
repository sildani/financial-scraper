from decimal import Decimal
from enum import Enum
from typing import Literal, Optional, List
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


class ParsedTransaction(BaseModel):
    """
    Gemini's parse of one Statement line: amount is positive for money out and Spend follows Category,
    until statement_summary() turns it into a Transaction.
    """
    transaction_date: str = Field(description="Transaction date in YYYY-MM-DD format")
    description: str = Field(description="Raw text descriptor from statement")
    clean_merchant: str = Field(description="Normalized vendor or entity name")
    amount: float = Field(description="Positive decimal value for spend/debits, negative for credits/payments/deposits")  # Money-out sign until statement_summary()
    category: SpendingCategory = Field(description="Categorization based on vendor or transaction type")

    # Methods, not fields, so they stay out of the Gemini output schema. Statement mode has no
    # Linked Movements, so Spend follows Category alone; Gemini's amounts are positive for money out.
    def spend(self) -> Optional[float]:
        if self.category in (SpendingCategory.PAYMENTS_CREDITS, SpendingCategory.INCOME_DEPOSITS):
            return None
        return self.amount

    def income(self) -> Optional[float]:
        return -self.amount if self.category == SpendingCategory.INCOME_DEPOSITS else None


class Transaction(ParsedTransaction):
    """
    A Transaction whose amount is its Displayed amount and whose Spend and Income are already decided:
    from Plaid's data and Linked Movements in Plaid mode, or from Gemini's money-out amount in Statement mode.
    """
    spend_amount: Optional[float] = None  # None when the Transaction isn't Spend
    income_amount: Optional[float] = None  # None when the Transaction isn't Income

    def spend(self) -> Optional[float]:
        return self.spend_amount

    def income(self) -> Optional[float]:
        return self.income_amount


class AccountSummary(BaseModel):
    institution: str = Field(description="Name of the financial institution, e.g., Amex, Citi, Chase, Bank of America, Goldman Sachs")
    account_name: str = Field(description="Account descriptor or last 4 digits (e.g., 'Amex Gold - 1004', 'Checking - 8821')")
    period: str = Field(description="Statement period string, e.g., 'Aug 01, 2026 - Aug 31, 2026'")
    account_kind: Literal["liability", "deposit"] = Field(description="'liability' for a credit card or loan statement, 'deposit' for checking or savings")
    balance: float = Field(description="The account's new or ending balance at the end of the statement period")
    amount_due: Optional[float] = Field(default=None, description="Total amount due, only for a credit card or loan statement; null for checking or savings")
    due_date: Optional[str] = Field(default=None, description="Payment due date in YYYY-MM-DD format, only for a credit card or loan statement; null for checking or savings")
    interest_charged: Optional[float] = Field(default=0.0, description="Total interest or fees charged, if applicable")
    transactions: List[ParsedTransaction] = Field(description="List of all transactions found in the statement")
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
