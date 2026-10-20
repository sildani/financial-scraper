from datetime import date
from pathlib import Path

from src.download_accounts import DownloadAccount
from src.downloads import Coverage, Download, Fetch, download
from src.export_window import ExportWindow
from src.llm import process_statement_pdf
from src.schemas import AccountSummary
from src.statements import statement_summary


class StatementAdapter:
    """Any Institution's Statement PDF, read by Gemini. The Download Account config names the Institution."""
    institution = None

    def __init__(self, parse=process_statement_pdf, force: bool = False):
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
        if isinstance(self._gemini_parses[file], Exception):
            raise self._gemini_parses[file]
        return self._gemini_parses[file]

    def fetch(self, window: ExportWindow) -> Fetch:
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
        return download(
            account, span, statement.transactions, window, statement.balance,
            period=statement.period, amount_due=statement.amount_due, due_date=statement.due_date,
            interest_charged=statement.interest_charged,
        )
