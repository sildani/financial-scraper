from src.schemas import AccountSummary, Transaction


def statement_summary(parsed: AccountSummary) -> AccountSummary:
    """
    A Statement as the Statement adapter exports it, from Gemini's parse. Gemini signs every amount positive
    for money out, which decides Spend and Income; the Displayed amount follows Account kind (ADR-0001).
    """
    sign = -1 if parsed.account_kind == "deposit" else 1
    transactions = [
        Transaction(
            **tx.model_dump(exclude={"amount"}),
            amount=sign * tx.amount,
            spend_amount=tx.spend(),
            income_amount=tx.income(),
        )
        for tx in parsed.transactions
    ]
    return parsed.model_copy(update={"transactions": transactions})
