from datetime import date, timedelta
from typing import Optional

from pydantic import BaseModel

DEFAULT_DAYS = 30
# Plaid gives a Link about 90 days of history unless its Link token requested more
DEFAULT_LINK_HISTORY_DAYS = 90


class ExportWindow(BaseModel):
    """The date range of Transactions an Export covers (GLOSSARY.md), both ends inclusive."""
    start: date
    end: date


def export_window(since: Optional[str], until: Optional[str], today: date) -> ExportWindow:
    end = parse_date("--until", until) if until else today
    start = parse_date("--since", since) if since else end - timedelta(days=DEFAULT_DAYS)
    if end > today:
        raise ValueError(f"--until {end} is in the future")
    if start > end:
        raise ValueError(f"--since {start} is after --until {end}")
    return ExportWindow(start=start, end=end)


def parse_date(flag: str, value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{flag} must be a date like 2026-01-31, not '{value}'") from None


def history_warning(window: ExportWindow, today: date) -> Optional[str]:
    """Links made before Link tokens requested more history may silently return less."""
    if window.start >= today - timedelta(days=DEFAULT_LINK_HISTORY_DAYS):
        return None
    return (
        f"--since {window.start} is more than {DEFAULT_LINK_HISTORY_DAYS} days ago. Links made before this tool "
        "requested 730 days of history only have about 90; re-link with --plaid-link to get more."
    )
