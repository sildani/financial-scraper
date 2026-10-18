from datetime import date
from typing import List, Literal, Tuple
from pydantic import BaseModel

from src.schemas import PlaidAccount, PlaidTransaction, link_institution

# Linked Movements (GLOSSARY.md) are inferred: two Transactions on different Accounts with
# exactly opposite amounts, a few days apart, whose Plaid categories both say money moved.
# Plaid's category is only a hint, so it never identifies a side alone. Taxonomy:
# https://plaid.com/documents/pfc-taxonomy-all.csv
# Some Institutions file the card side of a Card Payment under LOAN_DISBURSEMENTS_ (#33).
MOVEMENT_CATEGORY_PREFIXES = ("LOAN_PAYMENTS_", "LOAN_DISBURSEMENTS_", "TRANSFER_IN_", "TRANSFER_OUT_")
MAX_DAYS_APART = 5

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
        institution = link_institution(response["item"])
        accounts = {acc["account_id"]: PlaidAccount(**acc) for acc in response["accounts"]}
        for tx in (PlaidTransaction(**tx) for tx in response.get("transactions", [])):
            if not is_movement(tx):
                continue
            account = accounts[tx.account_id]
            candidates.append(Candidate(tx=tx, account=account, side=MovementSide(
                transaction_id=tx.transaction_id,
                institution=institution,
                account_name=account.label,
                date=tx.date,
                description=tx.name,
                amount=abs(tx.amount),
                # Plaid amounts are positive for money out on every Account kind, unlike the Displayed amount
                direction="paid" if tx.amount > 0 else "received",
            )))

    paying = sorted((c for c in candidates if c.side.direction == "paid"), key=lambda c: c.tx.date)
    receiving = [c for c in candidates if c.side.direction == "received"]

    # Closest pairs first across every candidate, so an earlier paying side that also fits
    # can't take a closer paying side's counterpart (#34). sorted() is stable, so ties keep date order.
    matches = sorted(
        (
            (paid, received) for paid in paying for received in receiving
            if received.tx.account_id != paid.tx.account_id
            and received.tx.amount == -paid.tx.amount
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
        paired.append((paid, received))

    unpaired = [c.side for c in paying + receiving if c.side.transaction_id not in used and is_own_account_movement(c)]
    return LinkedMovements(
        paired=[(paid.side, received.side) for paid, received in sorted(paired, key=lambda match: match[0].tx.date)],
        unpaired=sorted(unpaired, key=lambda s: s.date),
    )


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
    return c.account.kind == "liability" and c.side.direction == "received"
