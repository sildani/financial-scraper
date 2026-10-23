from datetime import date
from pathlib import Path
from typing import List, Mapping, Optional, Protocol

from pydantic import BaseModel

from src.assisted_downloads import (
    Assist,
    Watched,
    archive_download,
    recognized,
    watch_downloads,
)
from src.config import DOWNLOAD_ACCOUNTS_FILE, INPUT_DIR
from src.download_accounts import DownloadAccount
from src.export_window import ExportWindow
from src.schemas import AccountSummary, Transaction, account_key


class Coverage(BaseModel):
    """
    The dates a Download Account's file actually spans within the Export window, both ends inclusive.
    Before clipping to the window, the same shape holds the dates the file itself spans.
    """
    start: date
    end: date

    @property
    def label(self) -> str:
        return f"{self.start.isoformat()} to {self.end.isoformat()}"


class Download(BaseModel):
    """One Download Account's file, parsed: its Transactions inside the Export window, and its Coverage."""
    summary: AccountSummary
    coverage: Optional[Coverage]  # None when the file spans none of the window
    covered_to: Optional[date] = None  # The file's last date when it ends before the window does, which makes a Partial Export

    def balance_only(self) -> "Download":
        """The Download as a Balance-only Account takes it: its Balance and no Transactions, and Coverage doesn't apply, however old the file."""
        return Download(summary=self.summary.model_copy(update={"transactions": [], "coverage": None}), coverage=None)


class Adapter(Watched, Protocol):
    """Fetches and parses one vendor's Downloads."""
    @property
    def institution(self) -> Optional[str]:
        """None when the Download Account config names it, as for the Statement adapter."""

    def parse(self, file: Path, account: DownloadAccount, window: ExportWindow) -> Download: ...


def download(account: DownloadAccount, span: Coverage, transactions: List[Transaction], window: ExportWindow, balance: Optional[float] = None, **statement) -> Download:
    """
    A Download Account's file as the Export takes it: named by the Account, never by the file, with only the
    window's Transactions and the file's span clipped to the window as its Coverage. Its Balance is as of the
    file's last date, before clipping. statement holds what only a Statement provides, such as its own period
    and Amount due.
    """
    start, end = max(span.start, window.start), min(span.end, window.end)
    coverage = Coverage(start=start, end=end) if start <= end else None
    summary = AccountSummary(**{
        "institution": account.institution,
        "account_name": account.account_name,
        "period": coverage.label if coverage else "",
        "coverage": coverage.label if coverage else None,
        "account_kind": account.kind,
        "balance": balance,
        "balance_as_of": span.end if balance is not None else None,
        "transactions": [tx for tx in transactions if window.start <= date.fromisoformat(tx.transaction_date) <= window.end],
        **statement,
    })
    # Only the end counts: a late start may be a Statement period or an Account opened in the window
    return Download(summary=summary, coverage=coverage, covered_to=span.end if span.end < window.end else None)


class UnusableDownload(Exception):
    """A Download Account's file the Export can't take, which leaves the Account missing."""


def usable_download(adapter: Adapter, file: Path, account: DownloadAccount, window: ExportWindow) -> Download:
    """
    adapter's parse of file for account. Raises UnusableDownload, saying why, when the file can't be
    processed or, for a Balance-only Account, gives no Balance.
    """
    try:
        download = adapter.parse(file, account, window)
    except Exception as e:
        raise UnusableDownload(f"couldn't process {file.name}: {e}") from e
    if account.balance_only and download.summary.balance is None:
        raise UnusableDownload(f"{file.name} gives no Balance")
    return download


def exported_from_plaid(account: DownloadAccount, on_links: Optional[Mapping[tuple[str, str], str]]) -> bool:
    """Whether account is also among on_links, as plaid_transformer.accounts_on_links gives them, and says so."""
    link = (on_links or {}).get(account_key(account.institution, account.last4))
    if link:
        # Every Account has one Source, and Plaid wins (ADR-0003), so its page isn't opened either
        print(f"   ! {account.label} is also on Link {link}, so it's exported from Plaid; remove it from {DOWNLOAD_ACCOUNTS_FILE.name}")
    return bool(link)


class FetchedDownloads(BaseModel):
    downloads: List[Download]
    missing: List[DownloadAccount]  # Any missing Download Account makes the Export a Partial Export
    short: List[str] = []  # Each Download Account whose Coverage stops short, as a Partial Export names it


def fetch_downloads(accounts: List[DownloadAccount], files: List[Path], window: ExportWindow, adapters: dict[str, Adapter], assist: Optional[Assist] = None, on_links: Optional[Mapping[tuple[str, str], str]] = None) -> FetchedDownloads:
    """
    Parses each Download Account's file: the one among files its adapter recognizes or, failing that, an
    assisted Download through assist. An Account with no such file, or whose file can't be parsed, is missing
    instead of failing the run. An Account also among on_links, as plaid_transformer.accounts_on_links gives
    them, is skipped. A Balance-only Account takes only its file's Balance, and is missing when it has none.
    """
    downloads, missing, short, used = [], [], [], set()
    for account in accounts:
        adapter = adapters[account.adapter]
        matches = [file for file in sorted(files) if recognized(adapter, file, account)]
        used.update(matches)
        if exported_from_plaid(account, on_links):
            continue
        assisted_by = None  # The Assist that fetched the file, which then archives it
        if matches:
            # File names usually sort by date, so the last one is the latest
            *others, file = matches
            for other in others:
                print(f"   ! {account.label}: also matched {other.name}, which is not used")
        elif assist:
            file, assisted_by = watch_downloads(account, adapter, window, assist).file, assist
            if file is None:
                missing.append(account)
                continue
        else:
            missing.append(account)
            print(f"   ✗ {account.label}: no matching file in {INPUT_DIR.name}/")
            continue
        try:
            download, failure = usable_download(adapter, file, account, window), None
        except UnusableDownload as e:
            download, failure = None, e
        # Moved only once parsed, since the Statement adapter remembers Gemini's parse by the file's path
        moved = f", moved to {archive_download(file, account, window, assisted_by)}" if assisted_by else ""
        if download is None:
            missing.append(account)
            print(f"   ✗ {account.label}: {failure}{moved}")
            continue
        if account.balance_only:
            downloads.append(download.balance_only())
            print(f"   ✓ {account.label}: Balance as of {download.summary.balance_as_of} from {file.name}{moved}")
            continue
        downloads.append(download)
        print(f"   ✓ {account.label}: {len(download.summary.transactions)} transactions from {file.name}{moved}")
        if download.covered_to:
            covered_to = f"covered to {download.covered_to.isoformat()}"
            short.append(f"{account.label} {covered_to}")
            print(f"   ! {account.label}: {covered_to}, short of the Export window")

    for file in sorted(set(files) - used):
        print(f"   ! Not used: {file.name} matches no Download Account")
    return FetchedDownloads(downloads=downloads, missing=missing, short=short)
