from collections import defaultdict
from typing import Dict, List
from pydantic import BaseModel

from src.schemas import AccountSummary


class Analysis(BaseModel):
    """Totals over Spend, never Displayed amount (ADR-0001)."""
    spend_by_category: Dict[str, float]
    spend_by_merchant: Dict[str, float]
    total_spend: float
    income: float  # Reported separately, never netted against Spend


def analyze(summaries: List[AccountSummary]) -> Analysis:
    category_totals = defaultdict(float)
    merchant_totals = defaultdict(float)
    income = 0.0
    for s in summaries:
        for tx in s.transactions:
            income += tx.income() or 0.0
            spend = tx.spend()
            if spend is None:
                continue
            category_totals[tx.category.value] += spend
            merchant_totals[tx.clean_merchant] += spend

    return Analysis(
        spend_by_category=ranked(category_totals),
        spend_by_merchant=ranked(merchant_totals),
        total_spend=round(sum(category_totals.values()), 2),
        income=round(income, 2),
    )


def ranked(totals: Dict[str, float]) -> Dict[str, float]:
    return {key: round(total, 2) for key, total in sorted(totals.items(), key=lambda x: x[1], reverse=True)}
