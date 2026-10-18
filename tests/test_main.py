import contextlib
import io
import unittest

from main import parse_args, run_relink, run_statement_export
from src.plaid_client import FailedLink
from tests.test_analysis import statement
from tests.test_sheets import FakeSpreadsheet


def parse_error(argv) -> str:
    """parse_args' error message for argv, which must be rejected."""
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr), unittest.TestCase().assertRaises(SystemExit):
        parse_args(argv)
    return stderr.getvalue()


class ActionTest(unittest.TestCase):
    def test_a_bare_run_is_a_plaid_mode_export(self):
        self.assertEqual(parse_args([]).action, "plaid-export")

    def test_statements_flag_is_a_statement_mode_export(self):
        self.assertEqual(parse_args(["--statements"]).action, "statement-export")

    def test_plaid_link_flag_links(self):
        self.assertEqual(parse_args(["--plaid-link"]).action, "link")

    def test_relink_flag_relinks(self):
        self.assertEqual(parse_args(["--relink"]).action, "relink")


class FlagCombinationTest(unittest.TestCase):
    def test_a_run_does_one_thing(self):
        for pair in (["--statements", "--plaid-link"], ["--statements", "--relink"], ["--plaid-link", "--relink"]):
            with self.subTest(pair):
                self.assertIn("not allowed with argument", parse_error(pair))

    def test_force_belongs_to_statement_mode_only(self):
        self.assertTrue(parse_args(["--statements", "-f"]).force)
        for other_action in ([], ["--plaid-link"], ["--relink"]):
            with self.subTest(other_action):
                self.assertIn("-f/--force only applies with --statements", parse_error([*other_action, "-f"]))

    def test_export_window_belongs_to_plaid_mode_exports_only(self):
        self.assertEqual(parse_args(["--since", "2026-01-01", "--until", "2026-03-31"]).action, "plaid-export")
        for other_action in (["--statements"], ["--plaid-link"], ["--relink"]):
            with self.subTest(other_action):
                self.assertIn("--since and --until only apply to a Plaid mode Export", parse_error([*other_action, "--since", "2026-01-01"]))


def fake_parse(failing=()):
    """process_statement_pdf, raising for each PDF named in failing as Gemini or the cache might."""
    def parse(pdf_path, force_reprocess=False):
        if pdf_path.split("/")[-1] in failing:
            raise RuntimeError("status_code: 503, UNAVAILABLE")
        return statement("liability", ("Whole Foods", 60.00, "Groceries"))
    return parse


class StatementPartialExportTest(unittest.TestCase):
    def test_a_failed_statement_marks_the_export_incomplete_naming_its_file(self):
        spreadsheet = FakeSpreadsheet()
        with contextlib.redirect_stdout(io.StringIO()):
            export = run_statement_export(
                ["input/card-a.pdf", "input/card-b.pdf"], parse=fake_parse(failing=["card-b.pdf"]), spreadsheet=spreadsheet
            )
        banner = ["INCOMPLETE: missing card-b.pdf"]
        self.assertEqual(spreadsheet.tabs["Summary"].rows[0], banner)
        self.assertEqual(spreadsheet.tabs["Analysis"].rows[0], banner)
        self.assertEqual((len(export.summaries), export.failed), (1, ["card-b.pdf"]))

    def test_the_run_ends_by_naming_failed_files_and_how_to_retry_only_them(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            run_statement_export(["input/card-a.pdf", "input/card-b.pdf"], parse=fake_parse(failing=["card-b.pdf"]), spreadsheet=FakeSpreadsheet())
        last_lines = stdout.getvalue().strip().splitlines()[-2:]
        self.assertEqual(last_lines, [
            "! Partial Export: missing card-b.pdf",
            "   Run --statements again without -f to retry only the failed Statements; the rest are cached.",
        ])

    def test_no_export_is_written_when_every_statement_fails(self):
        spreadsheet = FakeSpreadsheet()
        with contextlib.redirect_stdout(io.StringIO()):
            export = run_statement_export(["input/card-a.pdf"], parse=fake_parse(failing=["card-a.pdf"]), spreadsheet=spreadsheet)
        self.assertEqual(spreadsheet.tabs, {})
        self.assertEqual((export.summaries, export.failed, export.tabs), ([], ["card-a.pdf"], []))


GINGHAM = FailedLink(item_id="item-gingham", institution="First Gingham Credit Union", error_code="ITEM_LOGIN_REQUIRED")
PLATYPUS = FailedLink(item_id="item-platypus", institution="First Platypus Bank", error_code="ITEM_LOGIN_REQUIRED")
TOKENS = {"item-gingham": "access-gingham", "item-platypus": "access-platypus"}


class FakeRelinkSessions:
    """Hosted Link sessions for --relink: each times out or finishes, and Plaid then reports error_codes."""
    def __init__(self, timing_out=(), error_codes=None):
        self.timing_out, self.error_codes, self.offered, self.checked = set(timing_out), error_codes or {}, [], []

    def session(self, access_token: str) -> bool:
        self.offered.append(access_token)
        return access_token not in self.timing_out

    def error_code(self, access_token: str) -> str | None:
        self.checked.append(access_token)
        return self.error_codes.get(access_token)


def relink(sessions: FakeRelinkSessions, needing=(GINGHAM, PLATYPUS)):
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        result = run_relink(list(needing), TOKENS, relink_session=sessions.session, error_code=sessions.error_code)
    return result, stdout.getvalue()


class RelinkTest(unittest.TestCase):
    def test_a_timed_out_link_still_needs_relinking_and_the_next_is_offered(self):
        sessions = FakeRelinkSessions(timing_out=["access-gingham"])
        result, _ = relink(sessions)
        self.assertEqual(sessions.offered, ["access-gingham", "access-platypus"])
        self.assertEqual((result.relinked, result.needs_relink), ([PLATYPUS], [GINGHAM]))

    def test_a_timed_out_link_is_not_checked_with_plaid(self):
        sessions = FakeRelinkSessions(timing_out=["access-gingham"])
        relink(sessions)
        self.assertEqual(sessions.checked, ["access-platypus"])

    def test_a_finished_session_still_needs_relinking_while_plaid_says_so(self):
        # The session ends even when sign-in failed or was abandoned
        sessions = FakeRelinkSessions(error_codes={"access-gingham": "ITEM_LOGIN_REQUIRED"})
        result, _ = relink(sessions)
        self.assertEqual((result.relinked, result.needs_relink), ([PLATYPUS], [GINGHAM]))

    def test_the_run_ends_by_listing_each_link_as_relinked_or_still_needing_it(self):
        _, stdout = relink(FakeRelinkSessions(timing_out=["access-gingham"]))
        self.assertEqual(stdout.strip().splitlines()[-4:], [
            "Re-link summary:",
            "   ✗ First Gingham Credit Union still needs re-linking",
            "   ✓ First Platypus Bank is re-linked",
            "Run --relink again to retry the Links that still need it.",
        ])


if __name__ == "__main__":
    unittest.main()
