from datetime import date
from pathlib import Path

from src.downloads import Download, Fetch, clip, download_summary, in_window
from src.export_window import ExportWindow
from src.llm import process_statement_pdf
from src.schemas import AccountSummary
from src.statements import statement_summary


class StatementAdapter:
    """Any Institution's Statement PDF, read by Gemini. The Download Account config names the Institution."""
    institution = None

    def __init__(self, parse=process_statement_pdf, force: bool = False):
        self._parse, self.force = parse, force
        self._parsed: dict[Path, AccountSummary | Exception] = {}

    def parsed(self, file: Path) -> AccountSummary:
        """
        Gemini's parse of file, tried once per run: recognizing it for every Account mustn't call Gemini
        again under -f, or retry a failed parse once per Account.
        """
        if file not in self._parsed:
            try:
                self._parsed[file] = self._parse(str(file), force_reprocess=self.force)
            except Exception as e:
                self._parsed[file] = e
        if isinstance(self._parsed[file], Exception):
            raise self._parsed[file]
        return self._parsed[file]

    def fetch(self, window: ExportWindow) -> Fetch:
        return Fetch(url=None, instructions=f"Download the Statement PDF that closes on or after {window.end.isoformat()}")

    def recognizes(self, file: Path, account) -> bool:
        # Gemini's account name ends with the Account's last four digits, e.g. 'Platypus Rewards - 1357'
        return file.suffix.lower() == ".pdf" and self.parsed(file).account_name.rstrip().endswith(account.last4)

    def parse(self, file: Path, account, window: ExportWindow) -> Download:
        parsed = self.parsed(file)
        if not parsed.period_start or not parsed.period_end:
            raise ValueError(f"its cached parse has no Statement period dates; re-parse it once with -f")
        coverage = clip(date.fromisoformat(parsed.period_start), date.fromisoformat(parsed.period_end), window)
        # The config's Account kind decides the Displayed amount's sign, not Gemini's guess (ADR-0001)
        statement = statement_summary(parsed.model_copy(update={"account_kind": account.kind}))
        transactions = [tx for tx in statement.transactions if in_window(tx, window)]
        return Download(
            summary=download_summary(
                account, coverage, transactions, statement.balance,
                period=statement.period, amount_due=statement.amount_due, due_date=statement.due_date,
                interest_charged=statement.interest_charged,
            ),
            coverage=coverage,
        )
