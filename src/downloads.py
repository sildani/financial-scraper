from datetime import date
from pathlib import Path
from typing import List, Optional, Protocol

from pydantic import BaseModel

from src.assisted_downloads import (
    Assist,
    Watched,
    archive_download,
    recognized,
    watch_downloads,
)
from src.config import INPUT_DIR
from src.download_accounts import DownloadAccount
from src.export_window import ExportWindow
from src.schemas import AccountSummary, Transaction


class Coverage(BaseModel):
    """
    The dates a Download Account's file actually spans within the Export window, both ends inclusive.
    Before clipping to the window, the same shape holds the dates the file itself spans.
    """
    start: date
    end: date


class Download(BaseModel):
    """One Download Account's file, parsed: its Transactions inside the Export window, and its Coverage."""
    summary: AccountSummary
    coverage: Optional[Coverage]  # None when the file spans none of the window


class Adapter(Watched, Protocol):
    """Fetches and parses one vendor's Downloads."""
    @property
    def institution(self) -> Optional[str]:
        """None when the Download Account config names it, as for the Statement adapter."""

    def parse(self, file: Path, account: DownloadAccount, window: ExportWindow) -> Download: ...


def download(account: DownloadAccount, span: Coverage, transactions: List[Transaction], window: ExportWindow, balance: Optional[float] = None, **statement) -> Download:
    """
    A Download Account's file as the Export takes it: named by the Account, never by the file, with only the
    window's Transactions and the file's span clipped to the window as its Coverage. statement holds what
    only a Statement provides, such as its own period and Amount due.
    """
    start, end = max(span.start, window.start), min(span.end, window.end)
    coverage = Coverage(start=start, end=end) if start <= end else None
    summary = AccountSummary(**{
        "institution": account.institution,
        "account_name": account.account_name,
        "period": f"{coverage.start.isoformat()} to {coverage.end.isoformat()}" if coverage else "",
        "account_kind": account.kind,
        "balance": balance,
        "transactions": [tx for tx in transactions if window.start <= date.fromisoformat(tx.transaction_date) <= window.end],
        **statement,
    })
    return Download(summary=summary, coverage=coverage)


class FetchedDownloads(BaseModel):
    downloads: List[Download]
    missing: List[DownloadAccount]  # Any missing Download Account makes the Export a Partial Export


def fetch_downloads(accounts: List[DownloadAccount], files: List[Path], window: ExportWindow, adapters: dict[str, Adapter], assist: Optional[Assist] = None) -> FetchedDownloads:
    """
    Parses each Download Account's file: the one among files its adapter recognizes or, failing that, an
    assisted Download through assist. An Account with no such file, or whose file can't be parsed, is missing
    instead of failing the run.
    """
    downloads, missing, used = [], [], set()
    for account in accounts:
        adapter = adapters[account.adapter]
        matches = [file for file in sorted(files) if recognized(adapter, file, account)]
        used.update(matches)
        assisted_by = None  # The Assist that fetched the file, which then archives it
        if matches:
            # File names usually sort by date, so the last one is the latest
            *others, file = matches
            for other in others:
                print(f"   ! {account.label}: also matched {other.name}, which is not used")
        elif assist:
            file, assisted_by = watch_downloads(account, adapter, window, assist), assist
            if file is None:
                missing.append(account)
                continue
        else:
            missing.append(account)
            print(f"   ✗ {account.label}: no matching file in {INPUT_DIR.name}/")
            continue
        try:
            download, failure = adapter.parse(file, account, window), None
        except Exception as e:
            download, failure = None, e
        # Moved only once parsed, since the Statement adapter remembers Gemini's parse by the file's path
        moved = f", moved to {archive_download(file, account, window, assisted_by)}" if assisted_by else ""
        if download is None:
            missing.append(account)
            print(f"   ✗ {account.label}: couldn't process {file.name}: {failure}{moved}")
            continue
        downloads.append(download)
        print(f"   ✓ {account.label}: {len(download.summary.transactions)} transactions from {file.name}{moved}")

    for file in sorted(set(files) - used):
        print(f"   ! Not used: {file.name} matches no Download Account")
    return FetchedDownloads(downloads=downloads, missing=missing)
