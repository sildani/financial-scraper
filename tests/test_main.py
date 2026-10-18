import contextlib
import io
import unittest

from main import parse_args


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


if __name__ == "__main__":
    unittest.main()
