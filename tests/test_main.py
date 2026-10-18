import contextlib
import io
import unittest

from main import parse_args, run_statement_export
from tests.test_analysis import statement
from tests.test_sheets import FakeSpreadsheet


def parse_error(argv) -> str:
    """parse_args' error message for argv, which must be rejected."""
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr), unittest.TestCase().assertRaises(SystemExit):
        parse_args(argv)
    return stderr.getvalue()


class ModeTest(unittest.TestCase):
    def test_a_bare_run_is_plaid_mode(self):
        self.assertEqual(parse_args([]).mode, "plaid")

    def test_statements_flag_is_statement_mode(self):
        self.assertEqual(parse_args(["--statements"]).mode, "statements")


class FlagCombinationTest(unittest.TestCase):
    def test_a_run_does_one_thing(self):
        for pair in (["--statements", "--plaid-link"], ["--statements", "--relink"], ["--plaid-link", "--relink"]):
            with self.subTest(pair):
                self.assertIn("not allowed with argument", parse_error(pair))

    def test_force_belongs_to_statement_mode_only(self):
        self.assertTrue(parse_args(["--statements", "-f"]).force)
        for other_mode in ([], ["--plaid-link"], ["--relink"]):
            with self.subTest(other_mode):
                self.assertIn("-f/--force only applies with --statements", parse_error([*other_mode, "-f"]))

    def test_export_window_belongs_to_plaid_mode_exports_only(self):
        self.assertEqual(parse_args(["--since", "2026-01-01", "--until", "2026-03-31"]).mode, "plaid")
        for other_mode in (["--statements"], ["--plaid-link"], ["--relink"]):
            with self.subTest(other_mode):
                self.assertIn("--since and --until only apply to a Plaid mode Export", parse_error([*other_mode, "--since", "2026-01-01"]))


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


if __name__ == "__main__":
    unittest.main()
