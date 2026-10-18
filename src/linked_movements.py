from datetime import date
from typing import List, Literal, Tuple
from pydantic import BaseModel

from src.schemas import PlaidAccount, PlaidTransaction

# Linked Movements (GLOSSARY.md) are inferred: two Transactions on different Accounts with
# exactly opposite amounts, a few days apart, whose Plaid categories both say money moved.
# Plaid's category is only a hint, so it never identifies a side alone. Taxonomy:
# https://plaid.com/documents/pfc-taxonomy-all.csv
MOVEMENT_CATEGORY_PREFIXES = ("LOAN_PAYMENTS_", "TRANSFER_IN_", "TRANSFER_OUT_")
MAX_DAYS_APART = 3
LIABILITY_TYPES = ("credit", "loan")

# Categories that mean money moved between two of your own Accounts, so a side without a
# counterpart is flagged. Other movement categories (Zelle, wires, withdrawals) can be money
# to or from someone else, so an unpaired one is left alone.
OWN_ACCOUNT_CATEGORY_PREFIXES = (
    "LOAN_PAYMENTS_",
    "TRANSFER_IN_ACCOUNT_TRANSFER",
    "TRANSFER_OUT_ACCOUNT_TRANSFER",
    "TRANSFER_IN_SAVINGS",
    "TRANSFER_OUT_SAVINGS",
)


class MovementSide(BaseModel):
    transaction_id: str
    institution: str
    account_name: str
    date: date
    description: str
    amount: float  # Money moved, always positive
    direction: Literal["paid", "received"]


class LinkedMovements(BaseModel):
    paired: List[Tuple[MovementSide, MovementSide]]  # (paying side, receiving side)
    unpaired: List[MovementSide]


def pair_linked_movements(responses: List[dict]) -> LinkedMovements:
    """
    Pairs the two sides of Card Payments, Internal Transfers and loan payments across every
    Account in every Link's /transactions/get response.
    """
    candidates = []
    for response in responses:
        item = response["item"]
        institution = item.get("institution_name") or item.get("institution_id") or "Unknown Institution"
        accounts = {acc["account_id"]: PlaidAccount(**acc) for acc in response["accounts"]}
        for tx in (PlaidTransaction(**tx) for tx in response.get("transactions", [])):
            if not is_movement(tx):
                continue
            account = accounts[tx.account_id]
            candidates.append(Candidate(tx=tx, account=account, side=MovementSide(
                transaction_id=tx.transaction_id,
                institution=institution,
                account_name=f"{account.name} ({account.mask})",
                date=tx.date,
                description=tx.name,
                amount=abs(tx.amount),
                # Plaid amounts are positive for money out on every Account kind (ADR-0001)
                direction="paid" if tx.amount > 0 else "received",
            )))

    paying = sorted((c for c in candidates if c.side.direction == "paid"), key=lambda c: c.tx.date)
    receiving = [c for c in candidates if c.side.direction == "received"]

    paired = []
    unpaired = []
    for paid in paying:
        matches = [
            c for c in receiving
            if c.tx.account_id != paid.tx.account_id
            and c.tx.amount == -paid.tx.amount
            and days_apart(c, paid) <= MAX_DAYS_APART
        ]
        if matches:
            closest = min(matches, key=lambda c: days_apart(c, paid))
            receiving.remove(closest)
            paired.append((paid.side, closest.side))
        elif is_own_account_movement(paid):
            unpaired.append(paid.side)

    unpaired.extend(c.side for c in receiving if is_own_account_movement(c))
    return LinkedMovements(paired=paired, unpaired=sorted(unpaired, key=lambda s: s.date))


class Candidate(BaseModel):
    tx: PlaidTransaction
    account: PlaidAccount
    side: MovementSide


def days_apart(a: Candidate, b: Candidate) -> int:
    return abs((a.tx.date - b.tx.date).days)


def is_movement(tx: PlaidTransaction) -> bool:
    if tx.pending or not tx.personal_finance_category:
        return False
    return tx.personal_finance_category.detailed.startswith(MOVEMENT_CATEGORY_PREFIXES)


def is_own_account_movement(c: Candidate) -> bool:
    if c.tx.personal_finance_category.detailed.startswith(OWN_ACCOUNT_CATEGORY_PREFIXES):
        return True
    # Money arriving on a Liability is a payment toward it, whatever Plaid calls it
    return c.account.type in LIABILITY_TYPES and c.side.direction == "received"
