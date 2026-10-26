from datetime import date
from typing import List, Literal, Sequence, Tuple

from pydantic import BaseModel

from src.schemas import (
    AccountSummary,
    PlaidAccount,
    PlaidTransaction,
    SpendingCategory,
    link_institution,
)

# Linked Movements (GLOSSARY.md) are inferred: two Transactions on different Accounts with
# exactly opposite amounts, a few days apart, whose categories both say money moved. Plaid's
# category is only a hint, so it never identifies a side alone; a Download has only its Category,
# which therefore does (see download_candidates). Taxonomy:
# https://plaid.com/documents/pfc-taxonomy-all.csv
# Some Institutions file the card side of a Card Payment under LOAN_DISBURSEMENTS_ (#33).
MOVEMENT_CATEGORY_PREFIXES = ("LOAN_PAYMENTS_", "LOAN_DISBURSEMENTS_", "TRANSFER_IN_", "TRANSFER_OUT_")
MAX_DAYS_APART = 5

# Categories that mean money moved between two of your own Accounts, so a side without a
# counterpart is flagged. Other movement categories (payment apps, wires, withdrawals) can be money
# to or from someone else, so an unpaired one is left alone.
OWN_ACCOUNT_CATEGORY_PREFIXES = (
    "LOAN_PAYMENTS_",
    "TRANSFER_IN_ACCOUNT_TRANSFER",
    "TRANSFER_OUT_ACCOUNT_TRANSFER",
    "TRANSFER_IN_SAVINGS",
    "TRANSFER_OUT_SAVINGS",
)

DOWNLOAD_MOVEMENT_CATEGORIES = (SpendingCategory.PAYMENTS_CREDITS, SpendingCategory.MORTGAGE_LOANS)

Direction = Literal["paid", "received"]


class MovementSide(BaseModel):
    transaction_id: str
    institution: str
    account_name: str
    date: date
    description: str
    amount: float  # Money moved, always positive
    direction: Direction


class LinkedMovements(BaseModel):
    paired: List[Tuple[MovementSide, MovementSide]]  # (paying side, receiving side)
    unpaired: List[MovementSide]


def pair_linked_movements(responses: List[dict], downloads: Sequence[AccountSummary] = ()) -> LinkedMovements:
    """
    Pairs the two sides of Card Payments, Internal Transfers and loan payments across every Account in
    the run, whatever its Source: every Account in every Link's /transactions/get response, and every
    Download Account's summary in downloads.
    """
    candidates = plaid_candidates(responses) + download_candidates(downloads)
    paying = sorted((c for c in candidates if c.side.direction == "paid"), key=lambda c: c.side.date)
    receiving = [c for c in candidates if c.side.direction == "received"]

    # Closest pairs first across every candidate, so an earlier paying side that also fits
    # can't take a closer paying side's counterpart (#34). sorted() is stable, so ties keep date order.
    matches = sorted(
        (
            (paid, received) for paid in paying for received in receiving
            if received.account_key != paid.account_key
            and received.side.amount == paid.side.amount
            and days_apart(received, paid) <= MAX_DAYS_APART
        ),
        key=lambda match: days_apart(*match),
    )
    paired = []
    used = set()
    for paid, received in matches:
        if paid.side.transaction_id in used or received.side.transaction_id in used:
            continue
        used.update((paid.side.transaction_id, received.side.transaction_id))
        paired.append((paid.side, received.side))

    unpaired = [c.side for c in paying + receiving if c.side.transaction_id not in used and c.own_account]
    return LinkedMovements(
        paired=sorted(paired, key=lambda match: match[0].date),
        unpaired=sorted(unpaired, key=lambda s: s.date),
    )


class Candidate(BaseModel):
    side: MovementSide
    # Only tells Accounts apart, since the two sides are on different ones: (Institution, Plaid's account_id)
    # from Plaid, (Institution, Account name) from a Download; one Source per Account keeps them apart (ADR-0003)
    account_key: Tuple[str, str]
    own_account: bool  # Money moved between two of your own Accounts, so it's flagged when unpaired


def plaid_candidates(responses: List[dict]) -> List[Candidate]:
    candidates = []
    for response in responses:
        institution = link_institution(response["item"])
        accounts = {acc["account_id"]: PlaidAccount(**acc) for acc in response["accounts"]}
        for tx in (PlaidTransaction(**tx) for tx in response.get("transactions", [])):
            if not is_movement(tx):
                continue
            account = accounts[tx.account_id]
            # Plaid amounts are positive for money out on every Account kind, unlike the Displayed amount
            side = movement_side(tx.transaction_id, institution, account.label, tx.date, tx.name, money_out=tx.amount)
            candidates.append(Candidate(
                side=side, account_key=(institution, tx.account_id), own_account=is_own_account_movement(tx, account, side.direction),
            ))
    return candidates


def download_candidates(downloads: Sequence[AccountSummary]) -> List[Candidate]:
    """
    A Download has no Plaid category, so its Category alone says money moved between your own Accounts:
    Payments & Credits, or Mortgage & Loans for a loan payment. An unpaired one is always flagged.
    """
    candidates = []
    for summary in downloads:
        for index, tx in enumerate(summary.transactions):
            if tx.category not in DOWNLOAD_MOVEMENT_CATEGORIES:
                continue
            # The Displayed amount's sign follows the Account's kind (ADR-0001)
            money_out = tx.amount if summary.account_kind == "liability" else -tx.amount
            side = movement_side(
                # A Download Transaction has no ID of its own
                f"{summary.institution} {summary.account_name} #{index}", summary.institution, summary.account_name,
                date.fromisoformat(tx.transaction_date), tx.description, money_out,
            )
            candidates.append(Candidate(side=side, account_key=(summary.institution, summary.account_name), own_account=True))
    return candidates


def movement_side(transaction_id: str, institution: str, account_name: str, on: date, description: str, money_out: float) -> MovementSide:
    return MovementSide(
        transaction_id=transaction_id, institution=institution, account_name=account_name, date=on, description=description,
        amount=abs(money_out), direction="paid" if money_out > 0 else "received",
    )


def days_apart(a: Candidate, b: Candidate) -> int:
    return abs((a.side.date - b.side.date).days)


def is_movement(tx: PlaidTransaction) -> bool:
    if tx.pending or not tx.personal_finance_category:
        return False
    return tx.personal_finance_category.detailed.startswith(MOVEMENT_CATEGORY_PREFIXES)


def is_own_account_movement(tx: PlaidTransaction, account: PlaidAccount, direction: Direction) -> bool:
    category = tx.personal_finance_category  # Set on every movement: is_movement requires it
    if category and category.detailed.startswith(OWN_ACCOUNT_CATEGORY_PREFIXES):
        return True
    # Money arriving on a Liability is a payment toward it, whatever Plaid calls it
    return account.kind == "liability" and direction == "received"
