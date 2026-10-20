from datetime import date
from pathlib import Path
from typing import List, Optional, Protocol
from pydantic import BaseModel

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


class Fetch(BaseModel):
    """Where to get an Account's Download, and what to pick there for an Export window."""
    url: Optional[str]  # None when the adapter can't know the page, as for any Institution's Statement
    instructions: str


class Download(BaseModel):
    """One Download Account's file, parsed: its Transactions inside the Export window, and its Coverage."""
    summary: AccountSummary
    coverage: Optional[Coverage]  # None when the file spans none of the window


class Adapter(Protocol):
    """Fetches and parses one vendor's Downloads."""
    institution: Optional[str]  # None when the Download Account config names it, as for the Statement adapter

    def fetch(self, window: ExportWindow) -> Fetch: ...

    def recognizes(self, file: Path, account: DownloadAccount) -> bool:
        """Whether file belongs to account, decided by its content, not its name."""

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


def fetch_downloads(accounts: List[DownloadAccount], files: List[Path], window: ExportWindow, adapters: dict[str, Adapter]) -> FetchedDownloads:
    """
    Parses each Download Account's file among files, the one its adapter recognizes. An Account with no
    such file, or whose file can't be parsed, is missing instead of failing the run.
    """
    downloads, missing, used = [], [], set()
    for account in accounts:
        adapter = adapters[account.adapter]
        matches = []
        for file in sorted(files):
            try:
                if adapter.recognizes(file, account):
                    matches.append(file)
            except Exception as e:
                print(f"   ! {account.label}: couldn't read {file.name}: {e}")
        used.update(matches)
        if not matches:
            missing.append(account)
            print(f"   ✗ {account.label}: no matching file in {INPUT_DIR.name}/")
            continue

        # File names usually sort by date, so the last one is the latest
        *others, file = matches
        for other in others:
            print(f"   ! {account.label}: also matched {other.name}, which is not used")
        try:
            download = adapter.parse(file, account, window)
        except Exception as e:
            missing.append(account)
            print(f"   ✗ {account.label}: couldn't process {file.name}: {e}")
            continue
        downloads.append(download)
        print(f"   ✓ {account.label}: {len(download.summary.transactions)} transactions from {file.name}")

    for file in sorted(set(files) - used):
        print(f"   ! Not used: {file.name} matches no Download Account")
    return FetchedDownloads(downloads=downloads, missing=missing)
