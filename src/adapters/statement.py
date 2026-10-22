from datetime import date
from pathlib import Path
from typing import Callable, Optional

from src.assisted_downloads import Fetch
from src.download_accounts import DownloadAccount
from src.downloads import Coverage, Download, download
from src.export_window import ExportWindow
from src.llm import process_statement_pdf
from src.schemas import AccountSummary
from src.statements import statement_summary


class StatementAdapter:
    """Any Institution's Statement PDF, read by Gemini. The Download Account config names the Institution."""
    institution: Optional[str] = None

    def __init__(self, parse: Callable[..., AccountSummary] = process_statement_pdf, force: bool = False):
        self._parse, self.force = parse, force
        self._gemini_parses: dict[Path, AccountSummary | Exception] = {}

    def gemini_parse(self, file: Path) -> AccountSummary:
        """
        Gemini's parse of file, tried once per run: recognizing it for every Account mustn't call Gemini
        again under -f, or retry a failed parse once per Account.
        """
        if file not in self._gemini_parses:
            try:
                self._gemini_parses[file] = self._parse(str(file), force_reprocess=self.force)
            except Exception as e:
                self._gemini_parses[file] = e
        parsed = self._gemini_parses[file]
        if isinstance(parsed, Exception):
            raise parsed
        return parsed

    def fetch(self, window: ExportWindow, account: DownloadAccount) -> Fetch:
        if account.balance_only:
            # A closed card may have no Statement that closes in the window, and only the Balance is used
            return Fetch(url=None, instructions="Download its latest Statement PDF; only its Balance is used")
        return Fetch(url=None, instructions=f"Download the Statement PDF that closes on or after {window.end.isoformat()}")

    def recognizes(self, file: Path, account: DownloadAccount) -> bool:
        # Gemini's account name ends with the Account's last four digits, e.g. 'Platypus Rewards - 1357'
        return file.suffix.lower() == ".pdf" and self.gemini_parse(file).account_name.rstrip().endswith(account.last4)

    def parse(self, file: Path, account: DownloadAccount, window: ExportWindow) -> Download:
        parsed = self.gemini_parse(file)
        if not parsed.period_start or not parsed.period_end:
            # A parse cached before Statement period dates were recorded lacks them, as can a fresh one
            raise ValueError("Gemini's parse has no Statement period dates; if it was cached before they were recorded, re-parse it once with -f")
        span = Coverage(start=date.fromisoformat(parsed.period_start), end=date.fromisoformat(parsed.period_end))
        # The config's Account kind decides the Displayed amount's sign, not Gemini's guess (ADR-0001)
        statement = statement_summary(parsed.model_copy(update={"account_kind": account.kind}))
        # statement_summary makes each one a Transaction, which AccountSummary's field type can't say
        return download(
            account, span, statement.transactions, window, statement.balance,  # pyright: ignore[reportArgumentType]
            period=statement.period, amount_due=statement.amount_due, due_date=statement.due_date,
            interest_charged=statement.interest_charged,
        )
