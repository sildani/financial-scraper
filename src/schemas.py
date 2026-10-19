from decimal import Decimal
from enum import Enum
from typing import Literal, NamedTuple, Optional, List
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


class SpendAndIncome(NamedTuple):
    spend: Optional[float]  # None when the Transaction isn't Spend
    income: Optional[float]  # None when the Transaction isn't Income


def category_spend_and_income(category: SpendingCategory, money_out: float) -> SpendAndIncome:
    """
    A Transaction's Spend and Income as its Category alone decides them, in both Modes, from its money-out
    amount. Payments & Credits (Card Payments, Internal Transfers and a loan's own side of a loan payment)
    is neither, and Income & Deposits is Income.
    """
    if category == SpendingCategory.INCOME_DEPOSITS:
        return SpendAndIncome(spend=None, income=-money_out)
    if category == SpendingCategory.PAYMENTS_CREDITS:
        return SpendAndIncome(spend=None, income=None)
    return SpendAndIncome(spend=money_out, income=None)


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
        return category_spend_and_income(self.category, self.amount).spend

    def income(self) -> Optional[float]:
        return category_spend_and_income(self.category, self.amount).income


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


AccountKind = Literal["liability", "deposit"]  # Liability or Deposit account (ADR-0001)
LIABILITY_TYPES = ("credit", "loan")  # The Plaid account types that are a Liability


class AccountSummary(BaseModel):
    institution: str = Field(description="Name of the financial institution, e.g., Amex, Citi, Chase, Bank of America, Goldman Sachs")
    account_name: str = Field(description="Account descriptor or last 4 digits (e.g., 'Amex Gold - 1004', 'Checking - 8821')")
    period: str = Field(description="Statement period string, e.g., 'Aug 01, 2026 - Aug 31, 2026'")
    account_kind: AccountKind = Field(description="'liability' for a credit card or loan statement, 'deposit' for checking or savings")
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

    @property
    def label(self) -> str:
        return f"{self.name} ({self.mask})"

    @property
    def kind(self) -> AccountKind:
        # Only a credit card or a loan is a Liability; any other Account holds your money (ADR-0001)
        return "liability" if self.type in LIABILITY_TYPES else "deposit"

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


def link_institution(item) -> str:
    """A Link's Institution name from Plaid's item, falling back to the Institution's ID, then the Link's item ID."""
    # Plaid documents institution_name and institution_id as nullable
    return item.get("institution_name") or item.get("institution_id") or item["item_id"]

def link_label(item_id: str, institution: str) -> str:
    """A Link as output names it: its Institution then its Link ID, or the Link ID alone when the Institution is unknown."""
    link_id = item_id[:6]
    # An unknown Institution falls back to the item ID, which the Link ID already shortens
    return link_id if institution == item_id else f"{institution} [{link_id}]"
