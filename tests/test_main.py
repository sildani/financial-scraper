import contextlib
import io
import unittest
from pathlib import Path

from main import Action, parse_args, run_export, run_relink
from src.download_accounts import DownloadAccount
from src.downloads import Download, download_summary
from src.plaid_client import FailedLink, RelinkCheck
from src.schemas import SpendingCategory, Transaction
from tests.test_plaid_client import FakeResponse, plaid_error
from tests.test_plaid_transformer import WINDOW, account, link_response
from tests.test_sheets import FakeSpreadsheet


def parse_error(argv) -> str:
    """parse_args' error message for argv, which must be rejected."""
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr), unittest.TestCase().assertRaises(SystemExit):
        parse_args(argv)
    return stderr.getvalue()


class ActionTest(unittest.TestCase):
    def test_a_bare_run_is_an_export(self):
        self.assertIs(parse_args([]).action, Action.EXPORT)

    def test_statements_flag_no_longer_exists(self):
        self.assertIn("unrecognized arguments: --statements", parse_error(["--statements"]))

    def test_plaid_link_flag_links(self):
        self.assertIs(parse_args(["--plaid-link"]).action, Action.LINK)

    def test_relink_flag_relinks(self):
        self.assertIs(parse_args(["--relink"]).action, Action.RELINK)


class FlagCombinationTest(unittest.TestCase):
    def test_a_run_does_one_thing(self):
        self.assertIn("not allowed with argument", parse_error(["--plaid-link", "--relink"]))

    def test_force_belongs_to_an_export_only(self):
        self.assertTrue(parse_args(["-f"]).force)
        for other_action in (["--plaid-link"], ["--relink"]):
            with self.subTest(other_action):
                self.assertIn("-f/--force only applies to an Export", parse_error([*other_action, "-f"]))

    def test_export_window_belongs_to_an_export_only(self):
        self.assertIs(parse_args(["--since", "2026-01-01", "--until", "2026-03-31"]).action, Action.EXPORT)
        for other_action in (["--plaid-link"], ["--relink"]):
            with self.subTest(other_action):
                self.assertIn("--since and --until only apply to an Export", parse_error([*other_action, "--since", "2026-01-01"]))


class FakeLinksClient:
    """Plaid for an Export: access tokens in failing raise that Plaid error code, every other
    returns one Account and no Transactions; /item/get names each Link's Institution from institutions."""
    def __init__(self, failing, institutions):
        self.failing, self.institutions = failing, institutions

    def item_get(self, request):
        return {"item": {"item_id": "item-1", "institution_id": "ins_1", "institution_name": self.institutions[request.access_token]}}

    def transactions_get(self, request):
        if request.access_token in self.failing:
            raise plaid_error(self.failing[request.access_token])
        return FakeResponse({**link_response([account()], []), "total_transactions": 0})


class PlaidPartialExportTest(unittest.TestCase):
    def test_two_failed_links_at_one_institution_are_told_apart_by_link_id(self):
        client = FakeLinksClient(
            failing={"access-first": "ITEM_LOGIN_REQUIRED", "access-second": "INSTITUTION_DOWN"},
            institutions={"access-first": "First Gingham Credit Union", "access-second": "First Gingham Credit Union"},
        )
        tokens = {"a1b2c3-gingham": "access-first", "d4e5f6-gingham": "access-second", "item-platypus": "access-platypus"}
        spreadsheet, stdout = FakeSpreadsheet(), io.StringIO()
        with contextlib.redirect_stdout(stdout):
            run_export(tokens, [], WINDOW, [], {}, spreadsheet=spreadsheet, client=client)
        lines = stdout.getvalue().splitlines()
        self.assertIn("   ✗ First Gingham Credit Union [a1b2c3] needs re-linking (run --relink)", lines)
        self.assertIn("   ✗ First Gingham Credit Union [d4e5f6] failed with INSTITUTION_DOWN; try again later", lines)
        missing = "missing First Gingham Credit Union [a1b2c3], First Gingham Credit Union [d4e5f6]"
        self.assertIn(f"! Partial Export: {missing}", lines)
        self.assertEqual(spreadsheet.tabs["Summary"].rows[0], [f"INCOMPLETE: {missing}"])
        self.assertEqual(spreadsheet.tabs["Analysis"].rows[0], [f"INCOMPLETE: {missing}"])


class FakeAdapter:
    """A vendor's adapter for First Tartan Bank: a file belongs to an Account when its name holds the Account's
    last four digits, and parsing gives one 20.00 Groceries purchase, or raises for a file named in failing."""
    institution = "First Tartan Bank"

    def __init__(self, failing=()):
        self.failing, self.parsed = failing, []

    def recognizes(self, file, account):
        return account.last4 in file.name

    def parse(self, file, account, window):
        self.parsed.append(file.name)
        if file.name in self.failing:
            raise ValueError("no header row")
        purchase = Transaction(
            transaction_date=window.end.isoformat(), description=f"FROM {file.name}", clean_merchant="Pinecone Market",
            amount=20.00, category=SpendingCategory.GROCERIES, spend_amount=20.00,
        )
        return Download(summary=download_summary(account, None, [purchase], 500.00), coverage=None)


def tartan(last4: str) -> DownloadAccount:
    return DownloadAccount(adapter="fake", institution="First Tartan Bank", last4=last4, kind="liability")


def export(accounts, input_files, adapter=None, tokens=None, client=None):
    """run_export over WINDOW with Links in tokens (none by default) and the fake adapter; returns it, the Sheet and stdout."""
    spreadsheet, stdout = FakeSpreadsheet(), io.StringIO()
    with contextlib.redirect_stdout(stdout):
        result = run_export(
            tokens or {}, accounts, WINDOW, [Path(f) for f in input_files], {"fake": adapter or FakeAdapter()},
            spreadsheet=spreadsheet, client=client or FakeLinksClient(failing={}, institutions={"access-platypus": "First Platypus Bank"}),
        )
    return result, spreadsheet, stdout.getvalue().splitlines()


class UnifiedExportTest(unittest.TestCase):
    def test_one_export_holds_plaid_accounts_and_download_accounts(self):
        result, spreadsheet, _ = export([tartan("2222")], ["input/tartan-2222.csv"], tokens={"item-platypus": "access-platypus"})
        summary_accounts = [row[:2] for row in spreadsheet.tabs["Summary"].rows[1:]]
        self.assertEqual(summary_accounts, [["First Platypus Bank", "Credit card (9496)"], ["First Tartan Bank", "••2222"]])
        self.assertIn(["First Tartan Bank", "••2222"], [row[:2] for row in spreadsheet.tabs["Details"].rows])
        self.assertEqual(result.missing, [])

    def test_a_download_account_with_no_file_makes_a_partial_export_naming_it(self):
        result, spreadsheet, lines = export([tartan("2222"), tartan("3333")], ["input/tartan-2222.csv"])
        missing = "missing First Tartan Bank ••3333"
        self.assertEqual(spreadsheet.tabs["Summary"].rows[0], [f"INCOMPLETE: {missing}"])
        self.assertEqual(spreadsheet.tabs["Analysis"].rows[0], [f"INCOMPLETE: {missing}"])
        self.assertIn("   ✗ First Tartan Bank ••3333: no matching file in input/", lines)
        self.assertIn(f"! Partial Export: {missing}", lines)
        self.assertEqual(result.missing, ["First Tartan Bank ••3333"])

    def test_a_download_that_cannot_be_parsed_makes_a_partial_export_naming_its_account(self):
        adapter = FakeAdapter(failing=["tartan-3333.csv"])
        result, _, lines = export([tartan("2222"), tartan("3333")], ["input/tartan-2222.csv", "input/tartan-3333.csv"], adapter=adapter)
        self.assertIn("   ✗ First Tartan Bank ••3333: couldn't process tartan-3333.csv: no header row", lines)
        self.assertEqual(result.missing, ["First Tartan Bank ••3333"])

    def test_a_partial_export_names_failed_links_before_missing_download_accounts(self):
        client = FakeLinksClient(failing={"access-gingham": "ITEM_LOGIN_REQUIRED"}, institutions={"access-gingham": "First Gingham Credit Union"})
        result, _, _ = export([tartan("3333")], [], tokens={"a1b2c3-gingham": "access-gingham"}, client=client)
        self.assertEqual(result.missing, ["First Gingham Credit Union [a1b2c3]", "First Tartan Bank ••3333"])

    def test_with_several_matching_files_the_last_by_name_is_used_and_the_others_named(self):
        adapter = FakeAdapter()
        _, _, lines = export([tartan("2222")], ["input/tartan-2222-09.csv", "input/tartan-2222-10.csv"], adapter=adapter)
        self.assertEqual(adapter.parsed, ["tartan-2222-10.csv"])
        self.assertIn("   ! First Tartan Bank ••2222: also matched tartan-2222-09.csv, which is not used", lines)

    def test_a_file_no_download_account_matches_is_named(self):
        _, _, lines = export([tartan("2222")], ["input/tartan-2222.csv", "input/notes.txt"])
        self.assertIn("   ! Not used: notes.txt matches no Download Account", lines)

    def test_no_export_is_written_when_nothing_could_be_fetched(self):
        result, spreadsheet, _ = export([tartan("3333")], [])
        self.assertEqual((spreadsheet.tabs, result.tabs, result.summaries), ({}, [], []))


GINGHAM = FailedLink(item_id="item-gingham", institution="First Gingham Credit Union", error_code="ITEM_LOGIN_REQUIRED")
PLATYPUS = FailedLink(item_id="item-platypus", institution="First Platypus Bank", error_code="ITEM_LOGIN_REQUIRED")
TOKENS = {"item-gingham": "access-gingham", "item-platypus": "access-platypus"}


class FakeRelinkSessions:
    """Hosted Link sessions for --relink: each times out, raises its Plaid error in session_errors,
    or finishes, and Plaid then reports error_codes or raises its error in check_errors."""
    def __init__(self, timing_out=(), error_codes=None, session_errors=None, check_errors=None):
        self.timing_out, self.error_codes, self.offered, self.checked = set(timing_out), error_codes or {}, [], []
        self.session_errors, self.check_errors = session_errors or {}, check_errors or {}

    def session(self, access_token: str) -> bool:
        self.offered.append(access_token)
        if access_token in self.session_errors:
            raise plaid_error(self.session_errors[access_token])
        return access_token not in self.timing_out

    def error_code(self, access_token: str) -> str | None:
        self.checked.append(access_token)
        if access_token in self.check_errors:
            raise plaid_error(self.check_errors[access_token])
        return self.error_codes.get(access_token)


def relink(sessions: FakeRelinkSessions, needing=(GINGHAM, PLATYPUS), unchecked=()):
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        check = RelinkCheck(needing=list(needing), unchecked=list(unchecked))
        result = run_relink(check, TOKENS, relink_session=sessions.session, error_code=sessions.error_code)
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
            "   ✗ First Gingham Credit Union [item-g] still needs re-linking",
            "   ✓ First Platypus Bank [item-p] is re-linked",
            "Run --relink again to retry the Links that still need it.",
        ])

    def test_a_plaid_error_during_a_session_leaves_that_link_needing_relinking_with_its_code(self):
        sessions = FakeRelinkSessions(session_errors={"access-gingham": "ITEM_NOT_FOUND"})
        result, stdout = relink(sessions)
        self.assertEqual(sessions.offered, ["access-gingham", "access-platypus"])
        self.assertEqual([(link.item_id, link.error_code) for link in result.needs_relink], [("item-gingham", "ITEM_NOT_FOUND")])
        self.assertEqual(result.relinked, [PLATYPUS])
        self.assertIn("   ✗ First Gingham Credit Union [item-g] still needs re-linking (ITEM_NOT_FOUND)", stdout.splitlines())

    def test_a_plaid_error_checking_a_finished_session_leaves_that_link_needing_relinking_with_its_code(self):
        sessions = FakeRelinkSessions(check_errors={"access-gingham": "INTERNAL_SERVER_ERROR"})
        result, stdout = relink(sessions)
        self.assertEqual(sessions.offered, ["access-gingham", "access-platypus"])
        self.assertEqual([(link.item_id, link.error_code) for link in result.needs_relink], [("item-gingham", "INTERNAL_SERVER_ERROR")])
        self.assertIn("   ✗ First Gingham Credit Union [item-g] still needs re-linking (INTERNAL_SERVER_ERROR)", stdout.splitlines())

    def test_a_finished_session_is_relinked_when_plaid_reports_an_error_relinking_cannot_fix(self):
        sessions = FakeRelinkSessions(error_codes={"access-gingham": "INSTITUTION_DOWN"})
        result, stdout = relink(sessions)
        self.assertEqual(([link.item_id for link in result.relinked], result.needs_relink), (["item-gingham", "item-platypus"], []))
        self.assertIn("   ✓ First Gingham Credit Union [item-g] is re-linked (Plaid reports INSTITUTION_DOWN)", stdout.splitlines())

    def test_two_links_at_one_institution_are_told_apart_by_link_id(self):
        first = FailedLink(item_id="a1b2c3-gingham", institution="First Gingham Credit Union", error_code="ITEM_LOGIN_REQUIRED")
        second = FailedLink(item_id="d4e5f6-gingham", institution="First Gingham Credit Union", error_code="ITEM_LOGIN_REQUIRED")
        tokens = {"a1b2c3-gingham": "access-first", "d4e5f6-gingham": "access-second"}
        sessions = FakeRelinkSessions(timing_out=["access-second"])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            run_relink(RelinkCheck(needing=[first, second], unchecked=[]), tokens, relink_session=sessions.session, error_code=sessions.error_code)
        lines = stdout.getvalue().splitlines()
        self.assertIn("--- Re-link First Gingham Credit Union [a1b2c3] ---", lines)
        self.assertIn("--- Re-link First Gingham Credit Union [d4e5f6] ---", lines)
        self.assertEqual(lines[-4:], [
            "Re-link summary:",
            "   ✓ First Gingham Credit Union [a1b2c3] is re-linked",
            "   ✗ First Gingham Credit Union [d4e5f6] still needs re-linking",
            "Run --relink again to retry the Links that still need it.",
        ])

    def test_a_link_that_could_not_be_checked_is_reported_first_and_in_the_summary(self):
        gone = FailedLink(item_id="item-gone", institution="item-gone", error_code="INVALID_ACCESS_TOKEN")
        result, stdout = relink(FakeRelinkSessions(), needing=[PLATYPUS], unchecked=[gone])
        self.assertEqual(result.unchecked, [gone])
        lines = stdout.splitlines()
        self.assertEqual(lines[0], "   ✗ item-g couldn't be checked (INVALID_ACCESS_TOKEN)")
        self.assertEqual(lines[-4:], [
            "Re-link summary:",
            "   ✗ item-g couldn't be checked (INVALID_ACCESS_TOKEN)",
            "   ✓ First Platypus Bank [item-p] is re-linked",
            "Run --relink again to retry the Links that still need it.",
        ])


if __name__ == "__main__":
    unittest.main()
