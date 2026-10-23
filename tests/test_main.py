import contextlib
import io
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from main import (
    Action,
    parse_args,
    reject_download_flags,
    run_check_downloads,
    run_export,
    run_relink,
)
from src.assisted_downloads import Assist, Fetch
from src.download_accounts import DownloadAccount
from src.downloads import Coverage, download
from src.plaid_client import FailedLink, RelinkCheck
from src.schemas import SpendingCategory, Transaction
from tests.test_assisted_downloads import START, FakeUser, lands, tartan
from tests.test_plaid_client import FakeResponse, plaid_error
from tests.test_plaid_transformer import EXPORTED_ON, WINDOW, account, link_response
from tests.test_sheets import FakeSpreadsheet

DAY_S = 24 * 60 * 60


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

    def test_check_downloads_flag_checks_every_adapter_or_the_one_named(self):
        args = parse_args(["--check-downloads"])
        self.assertEqual((args.action, args.adapter), (Action.CHECK_DOWNLOADS, None))
        args = parse_args(["--check-downloads", "first_gingham"])
        self.assertEqual((args.action, args.adapter), (Action.CHECK_DOWNLOADS, "first_gingham"))

    def test_skip_downloads_is_an_export(self):
        args = parse_args(["--skip-downloads"])
        self.assertEqual((args.action, args.skip_downloads), (Action.EXPORT, True))


class FlagCombinationTest(unittest.TestCase):
    def test_a_run_does_one_thing(self):
        self.assertIn("not allowed with argument", parse_error(["--plaid-link", "--relink"]))

    def test_force_belongs_to_an_export_only(self):
        self.assertTrue(parse_args(["-f"]).force)
        for other_action in (["--plaid-link"], ["--relink"]):
            with self.subTest(other_action):
                self.assertIn("-f/--force only applies to an Export", parse_error([*other_action, "-f"]))

    def test_check_downloads_does_not_link_or_relink(self):
        for other_action in (["--plaid-link"], ["--relink"]):
            with self.subTest(other_action):
                self.assertIn("not allowed with argument", parse_error([*other_action, "--check-downloads"]))

    def test_skip_downloads_belongs_to_an_export_only(self):
        for other_action in (["--plaid-link"], ["--relink"], ["--check-downloads"]):
            with self.subTest(other_action):
                self.assertIn("--skip-downloads only applies to an Export", parse_error([*other_action, "--skip-downloads"]))

    def test_check_downloads_takes_its_own_window(self):
        self.assertIn("--since and --until only apply to an Export", parse_error(["--check-downloads", "--until", "2026-10-01"]))

    def test_download_flags_are_rejected_in_sandbox_where_there_are_no_download_accounts(self):
        for flags in (["--skip-downloads"], ["--check-downloads"]):
            with self.subTest(flags), self.assertRaisesRegex(ValueError, f"{flags[0]} only applies in Production, where Download Accounts exist"):
                reject_download_flags(parse_args(flags), "sandbox")
            reject_download_flags(parse_args(flags), "production")
        reject_download_flags(parse_args([]), "sandbox")

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
            run_export(tokens, [], WINDOW, [], adapters={}, spreadsheet=spreadsheet, client=client)
        lines = stdout.getvalue().splitlines()
        self.assertIn("   ✗ First Gingham Credit Union [a1b2c3] needs re-linking (run --relink)", lines)
        self.assertIn("   ✗ First Gingham Credit Union [d4e5f6] failed with INSTITUTION_DOWN; try again later", lines)
        missing = "missing First Gingham Credit Union [a1b2c3], First Gingham Credit Union [d4e5f6]"
        self.assertIn(f"! Partial Export: {missing}", lines)
        self.assertEqual(spreadsheet.tabs["Summary"].rows[0], [f"INCOMPLETE: {missing}"])
        self.assertEqual(spreadsheet.tabs["Analysis"].rows[0], [f"INCOMPLETE: {missing}"])


class FakeAdapter:
    """A vendor's adapter for First Tartan Bank: a file belongs to an Account when its name holds the Account's
    last four digits, and parsing gives one 20.00 Groceries purchase, or raises for a file named in failing.
    A file spans the whole Export window unless spans gives its (start, end) by name, and its page is url.
    Its Balance is 500.00, or None for a file named in no_balance."""
    institution = "First Tartan Bank"

    def __init__(self, failing=(), spans=None, url=None, no_balance=()):
        self.failing, self.spans, self.url, self.no_balance, self.parsed = failing, spans or {}, url, no_balance, []

    def fetch(self, window, account):
        return Fetch(url=self.url, instructions="Download it")

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
        start, end = self.spans.get(file.name, (window.start, window.end))
        return download(account, Coverage(start=start, end=end), [purchase], window, None if file.name in self.no_balance else 500.00)


def export(accounts, input_files, adapter=None, tokens=None, client=None, assist=None, skip_downloads=False):
    """run_export over WINDOW with Links in tokens (none by default) and the fake adapter; returns it, the Sheet and stdout."""
    spreadsheet, stdout = FakeSpreadsheet(), io.StringIO()
    with contextlib.redirect_stdout(stdout):
        result = run_export(
            tokens or {}, accounts, WINDOW, [Path(f) for f in input_files], adapters={"fake": adapter or FakeAdapter()},
            spreadsheet=spreadsheet, client=client or FakeLinksClient(failing={}, institutions={"access-platypus": "First Platypus Bank"}),
            assist=assist, exported_on=EXPORTED_ON, skip_downloads=skip_downloads,
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

    def assist(self, temp: str) -> Assist:
        downloads_dir = Path(temp) / "Downloads"
        downloads_dir.mkdir()
        user = FakeUser(downloads_dir, [])
        return Assist(downloads_dir=downloads_dir, archive_dir=Path(temp) / ".downloads", open_page=user.open_page, clock=user)

    def test_an_assisted_download_that_never_lands_makes_a_partial_export_naming_its_account(self):
        with tempfile.TemporaryDirectory() as temp:
            result, spreadsheet, _ = export([tartan("2222"), tartan("3333")], ["input/tartan-2222.csv"], assist=self.assist(temp))
        self.assertEqual(result.missing, ["First Tartan Bank ••3333"])
        self.assertEqual(spreadsheet.tabs["Summary"].rows[0], ["INCOMPLETE: missing First Tartan Bank ••3333"])

    def test_archived_downloads_older_than_90_days_are_pruned_at_the_start(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / ".downloads" / "first-tartan-bank-2222"
            folder.mkdir(parents=True)
            for name, days in [("2026-06-01_to_2026-07-01.csv", 91), ("2026-07-01_to_2026-08-01.csv", 89)]:
                (folder / name).write_text("First Tartan Bank 2222\n")
                os.utime(folder / name, (START - days * DAY_S, START - days * DAY_S))
            export([], [], assist=self.assist(temp))
            self.assertEqual([f.name for f in folder.iterdir()], ["2026-07-01_to_2026-08-01.csv"])

    def test_no_export_is_written_when_nothing_could_be_fetched(self):
        result, spreadsheet, _ = export([tartan("3333")], [])
        self.assertEqual((spreadsheet.tabs, result.tabs, result.summaries), ({}, [], []))


class CoverageTest(unittest.TestCase):
    def test_summary_shows_each_download_accounts_coverage_and_the_window_for_plaid_accounts(self):
        adapter = FakeAdapter(spans={"tartan-2222.csv": (date(2026, 9, 1), date(2026, 10, 30))})
        _, spreadsheet, _ = export([tartan("2222")], ["input/tartan-2222.csv"], adapter=adapter, tokens={"item-platypus": "access-platypus"})
        header, *rows = spreadsheet.tabs["Summary"].rows
        self.assertEqual(header[:4], ["Institution", "Account Name", "Period", "Coverage"])
        self.assertEqual([row[:4] for row in rows], [
            ["First Platypus Bank", "Credit card (9496)", "2026-09-17 to 2026-10-17", "2026-09-17 to 2026-10-17"],
            ["First Tartan Bank", "••2222", "2026-09-17 to 2026-10-17", "2026-09-17 to 2026-10-17"],
        ])

    def test_coverage_that_stops_short_of_the_window_makes_a_partial_export_naming_the_date_it_reaches(self):
        adapter = FakeAdapter(spans={"tartan-2222.csv": (date(2026, 9, 1), date(2026, 10, 10))})
        result, spreadsheet, lines = export([tartan("2222")], ["input/tartan-2222.csv"], adapter=adapter)
        # Its file was exported, so it isn't named as missing
        short = "First Tartan Bank ••2222 covered to 2026-10-10"
        self.assertEqual((result.missing, result.short), ([], [short]))
        self.assertEqual(spreadsheet.tabs["Summary"].rows[0], [f"INCOMPLETE: {short}"])
        self.assertEqual(spreadsheet.tabs["Analysis"].rows[0], [f"INCOMPLETE: {short}"])
        self.assertEqual(spreadsheet.tabs["Summary"].rows[2][3], "2026-09-17 to 2026-10-10")
        self.assertIn("   ! First Tartan Bank ••2222: covered to 2026-10-10, short of the Export window", lines)
        self.assertIn(f"! Partial Export: {short}", lines)

    def test_coverage_that_starts_late_is_not_short(self):
        adapter = FakeAdapter(spans={"tartan-2222.csv": (date(2026, 10, 1), date(2026, 10, 17))})
        result, _, _ = export([tartan("2222")], ["input/tartan-2222.csv"], adapter=adapter)
        self.assertEqual((result.missing, result.short), ([], []))

    def test_a_file_that_ends_before_the_window_is_named_by_its_last_date_with_blank_coverage(self):
        adapter = FakeAdapter(spans={"tartan-2222.csv": (date(2026, 8, 1), date(2026, 9, 10))})
        result, spreadsheet, _ = export([tartan("2222")], ["input/tartan-2222.csv"], adapter=adapter)
        self.assertEqual(result.short, ["First Tartan Bank ••2222 covered to 2026-09-10"])
        self.assertEqual(spreadsheet.tabs["Summary"].rows[2][:4], ["First Tartan Bank", "••2222", "", ""])

    def test_short_coverage_is_named_after_failed_links_and_missing_download_accounts(self):
        adapter = FakeAdapter(spans={"tartan-2222.csv": (date(2026, 9, 1), date(2026, 10, 10))})
        client = FakeLinksClient(failing={"access-gingham": "ITEM_LOGIN_REQUIRED"}, institutions={"access-gingham": "First Gingham Credit Union"})
        _, spreadsheet, lines = export(
            [tartan("2222"), tartan("3333")], ["input/tartan-2222.csv"], adapter=adapter, tokens={"a1b2c3-gingham": "access-gingham"}, client=client,
        )
        incomplete = "missing First Gingham Credit Union [a1b2c3], First Tartan Bank ••3333; First Tartan Bank ••2222 covered to 2026-10-10"
        self.assertEqual(spreadsheet.tabs["Summary"].rows[0], [f"INCOMPLETE: {incomplete}"])
        self.assertIn(f"! Partial Export: {incomplete}", lines)


class BalanceAsOfTest(unittest.TestCase):
    def test_summary_shows_each_balance_with_its_as_of_date(self):
        adapter = FakeAdapter(spans={"tartan-2222.csv": (date(2026, 9, 1), date(2026, 10, 30))})
        _, spreadsheet, _ = export([tartan("2222")], ["input/tartan-2222.csv"], adapter=adapter, tokens={"item-platypus": "access-platypus"})
        header, *rows = spreadsheet.tabs["Summary"].rows
        self.assertEqual(header[4:6], ["Balance", "Balance As Of"])
        # Plaid's Balance is as of the Export, and a Download's where its file ends, past the window here
        self.assertEqual([row[4:6] for row in rows], [[1250.00, "2026-10-21"], [500.00, "2026-10-30"]])

    def test_a_download_without_a_balance_shows_it_blank(self):
        adapter = FakeAdapter(no_balance=["tartan-2222.csv"])
        _, spreadsheet, _ = export([tartan("2222")], ["input/tartan-2222.csv"], adapter=adapter)
        self.assertEqual(spreadsheet.tabs["Summary"].rows[1][4:6], [None, ""])


class BalanceOnlyTest(unittest.TestCase):
    def balance_only(self, last4):
        return tartan(last4).model_copy(update={"balance_only": True})

    def test_a_balance_only_account_shows_its_balance_and_as_of_date_with_no_transactions_or_coverage(self):
        # A closed card's latest Statement ends long before the window
        adapter = FakeAdapter(spans={"tartan-2222.csv": (date(2026, 7, 1), date(2026, 7, 31))})
        result, spreadsheet, lines = export([self.balance_only("2222")], ["input/tartan-2222.csv"], adapter=adapter)
        header, row = spreadsheet.tabs["Summary"].rows
        self.assertEqual(header[3:6], ["Coverage", "Balance", "Balance As Of"])
        self.assertEqual((row[3:6], row[-1]), (["", 500.00, "2026-07-31"], 0))
        self.assertEqual(spreadsheet.tabs["Details"].rows[1:], [])
        self.assertIn("   ✓ First Tartan Bank ••2222: Balance as of 2026-07-31 from tartan-2222.csv", lines)

    def test_a_balance_only_account_never_makes_a_partial_export_for_coverage(self):
        adapter = FakeAdapter(spans={"tartan-2222.csv": (date(2026, 7, 1), date(2026, 7, 31))})
        result, spreadsheet, _ = export([self.balance_only("2222")], ["input/tartan-2222.csv"], adapter=adapter)
        self.assertEqual((result.missing, result.short), ([], []))
        self.assertEqual(spreadsheet.tabs["Summary"].rows[0][0], "Institution")

    def test_a_balance_only_account_whose_balance_cant_be_obtained_makes_a_partial_export_naming_it(self):
        adapter = FakeAdapter(no_balance=["tartan-2222.csv"])
        result, spreadsheet, lines = export([self.balance_only("2222"), tartan("3333")], ["input/tartan-2222.csv", "input/tartan-3333.csv"], adapter=adapter)
        missing = "missing First Tartan Bank ••2222"
        self.assertEqual(result.missing, ["First Tartan Bank ••2222"])
        self.assertEqual(spreadsheet.tabs["Summary"].rows[0], [f"INCOMPLETE: {missing}"])
        self.assertEqual([row[1] for row in spreadsheet.tabs["Summary"].rows[2:]], ["••3333"])
        self.assertIn("   ✗ First Tartan Bank ••2222: tartan-2222.csv gives no Balance", lines)


class OverlapTest(unittest.TestCase):
    # On the Link FakeLinksClient returns, under another case, as an adapter may spell its Institution
    ON_PLAID = DownloadAccount(adapter="fake", institution="FIRST PLATYPUS BANK", last4="9496", kind="liability")

    def test_an_account_on_a_link_and_in_the_config_is_exported_once_from_plaid_with_a_warning_and_its_page_never_opens(self):
        with tempfile.TemporaryDirectory() as temp:
            downloads_dir = Path(temp) / "Downloads"
            downloads_dir.mkdir()
            user = FakeUser(downloads_dir, [])
            assist = Assist(downloads_dir=downloads_dir, archive_dir=Path(temp) / ".downloads", open_page=user.open_page, clock=user)
            result, spreadsheet, lines = export(
                [self.ON_PLAID], [], adapter=FakeAdapter(url="https://tartan.example/export"), tokens={"item-platypus": "access-platypus"}, assist=assist,
            )
        self.assertEqual(user.opened, [])
        self.assertEqual([row[:2] for row in spreadsheet.tabs["Summary"].rows[1:]], [["First Platypus Bank", "Credit card (9496)"]])
        self.assertEqual(result.missing, [])
        self.assertIn(
            "   ! FIRST PLATYPUS BANK ••9496 is also on Link First Platypus Bank [item-1], so it's exported from Plaid; "
            "remove it from download_accounts.toml", lines,
        )

    def test_when_its_link_fails_the_download_is_used_since_plaid_has_no_copy(self):
        client = FakeLinksClient(failing={"access-platypus": "ITEM_LOGIN_REQUIRED"}, institutions={"access-platypus": "First Platypus Bank"})
        result, spreadsheet, _ = export([self.ON_PLAID], ["input/platypus-9496.csv"], tokens={"item-platypus": "access-platypus"}, client=client)
        self.assertEqual([row[:2] for row in spreadsheet.tabs["Summary"].rows[2:]], [["FIRST PLATYPUS BANK", "••9496"]])
        self.assertEqual(result.missing, ["First Platypus Bank [item-p]"])

    def test_its_file_in_input_is_not_parsed_or_reported_as_unused(self):
        adapter = FakeAdapter()
        _, _, lines = export([self.ON_PLAID], ["input/platypus-9496.csv"], adapter=adapter, tokens={"item-platypus": "access-platypus"})
        self.assertEqual(adapter.parsed, [])
        self.assertNotIn("   ! Not used: platypus-9496.csv matches no Download Account", lines)

    def test_the_same_last_four_digits_at_another_institution_is_not_an_overlap(self):
        result, spreadsheet, _ = export([tartan("9496")], ["input/tartan-9496.csv"], tokens={"item-platypus": "access-platypus"})
        self.assertEqual([row[:2] for row in spreadsheet.tabs["Summary"].rows[1:]], [["First Platypus Bank", "Credit card (9496)"], ["First Tartan Bank", "••9496"]])


class SkipDownloadsTest(unittest.TestCase):
    def test_skipping_downloads_makes_a_partial_export_naming_every_download_account_and_opens_no_page(self):
        adapter = FakeAdapter(url="https://tartan.example/export")
        with tempfile.TemporaryDirectory() as temp:
            downloads_dir = Path(temp) / "Downloads"
            downloads_dir.mkdir()
            user = FakeUser(downloads_dir, [])
            assist = Assist(downloads_dir=downloads_dir, archive_dir=Path(temp) / ".downloads", open_page=user.open_page, clock=user)
            result, spreadsheet, lines = export(
                [tartan("2222"), tartan("3333")], ["input/tartan-2222.csv"], adapter=adapter,
                tokens={"item-platypus": "access-platypus"}, assist=assist, skip_downloads=True,
            )
        missing = "missing First Tartan Bank ••2222, First Tartan Bank ••3333"
        self.assertEqual(result.missing, ["First Tartan Bank ••2222", "First Tartan Bank ••3333"])
        self.assertEqual(spreadsheet.tabs["Summary"].rows[0], [f"INCOMPLETE: {missing}"])
        self.assertEqual([row[:2] for row in spreadsheet.tabs["Summary"].rows[2:]], [["First Platypus Bank", "Credit card (9496)"]])
        self.assertEqual((user.opened, adapter.parsed), ([], []))
        self.assertIn("   ✗ First Tartan Bank ••2222: skipped (--skip-downloads)", lines)
        self.assertIn(f"! Partial Export: {missing}", lines)

    def test_an_account_also_on_a_link_is_not_named_missing_and_is_warned_about_as_in_any_export(self):
        result, _, lines = export([OverlapTest.ON_PLAID], [], tokens={"item-platypus": "access-platypus"}, skip_downloads=True)
        self.assertEqual(result.missing, [])
        self.assertIn(
            "   ! FIRST PLATYPUS BANK ••9496 is also on Link First Platypus Bank [item-1], so it's exported from Plaid; "
            "remove it from download_accounts.toml", lines,
        )


class CheckDownloadsTest(unittest.TestCase):
    TODAY = date(2026, 10, 21)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.downloads_dir, self.archive_dir = Path(temp.name) / "Downloads", Path(temp.name) / ".downloads"
        self.downloads_dir.mkdir()

    def check(self, accounts, plans, adapter=None, only=None):
        """run_check_downloads with the fake adapter and you doing each of plans in turn; returns it, you and stdout."""
        user, stdout = FakeUser(self.downloads_dir, plans), io.StringIO()
        assist = Assist(downloads_dir=self.downloads_dir, archive_dir=self.archive_dir, open_page=user.open_page, clock=user)
        with contextlib.redirect_stdout(stdout):
            result = run_check_downloads(accounts, {"fake": adapter or FakeAdapter(url="https://tartan.example/export")}, self.TODAY, assist, only=only)
        return result, user, stdout.getvalue().splitlines()

    def test_a_file_that_lands_is_reported_with_every_date_it_spans_and_left_where_it_is(self):
        # Past both ends of the check's window, which mustn't clip it
        adapter = FakeAdapter(url="https://tartan.example/export", spans={"tartan-2222.csv": (date(2026, 8, 1), date(2026, 10, 30))})
        result, user, lines = self.check([tartan("2222")], [[(10, lands("tartan-2222.csv", "Date,Amount\n"))]], adapter=adapter)
        self.assertEqual(result.window.model_dump(), {"start": date(2026, 10, 14), "end": self.TODAY})
        [account] = result.accounts
        self.assertEqual(
            (account.account, account.file, account.span, account.balance_as_of),
            ("First Tartan Bank ••2222", "tartan-2222.csv", Coverage(start=date(2026, 8, 1), end=date(2026, 10, 30)), date(2026, 10, 30)),
        )
        self.assertTrue(result.passed)
        self.assertEqual(user.opened, ["https://tartan.example/export"])
        self.assertTrue((self.downloads_dir / "tartan-2222.csv").exists())
        self.assertFalse(self.archive_dir.exists())
        self.assertIn("   ✓ First Tartan Bank ••2222: tartan-2222.csv spans 2026-08-01 to 2026-10-30, Balance as of 2026-10-30", lines)

    def test_an_account_whose_file_never_arrives_fails_the_check_naming_any_file_that_landed_unrecognized(self):
        result, _, _ = self.check([tartan("2222"), tartan("3333")], [
            [(10, lands("tartan-9999.csv", "Date,Amount\n"))],
            [(10, lands("tartan-3333.csv", "Date,Amount\n"))],
        ])
        self.assertEqual(
            [(a.account, a.file, a.unrecognized) for a in result.accounts],
            [("First Tartan Bank ••2222", None, ["tartan-9999.csv"]), ("First Tartan Bank ••3333", "tartan-3333.csv", [])],
        )
        self.assertFalse(result.passed)

    def test_a_recognized_file_that_cant_be_processed_fails_the_check_and_the_next_account_is_still_checked(self):
        adapter = FakeAdapter(url="https://tartan.example/export", failing=["tartan-2222.csv"])
        result, _, lines = self.check([tartan("2222"), tartan("3333")], [
            [(10, lands("tartan-2222.csv", "Date,Amount\n"))],
            [(10, lands("tartan-3333.csv", "Date,Amount\n"))],
        ], adapter=adapter)
        self.assertEqual(
            [(a.file, a.failure, a.span is None) for a in result.accounts],
            [("tartan-2222.csv", "couldn't process tartan-2222.csv: no header row", True), ("tartan-3333.csv", None, False)],
        )
        self.assertFalse(result.passed)
        self.assertIn("   ✗ First Tartan Bank ••2222: couldn't process tartan-2222.csv: no header row", lines)

    def test_a_balance_only_account_whose_file_gives_no_balance_fails_the_check(self):
        adapter = FakeAdapter(url="https://tartan.example/export", no_balance=["tartan-2222.csv"])
        balance_only = tartan("2222").model_copy(update={"balance_only": True})
        result, _, lines = self.check([balance_only], [[(10, lands("tartan-2222.csv", "Date,Amount\n"))]], adapter=adapter)
        self.assertEqual(result.accounts[0].failure, "tartan-2222.csv gives no Balance")
        self.assertFalse(result.passed)
        self.assertIn("   ✗ First Tartan Bank ••2222: tartan-2222.csv gives no Balance", lines)

    def test_naming_an_adapter_checks_only_its_download_accounts(self):
        gingham = DownloadAccount(adapter="gingham", institution="First Gingham Credit Union", last4="4321", kind="deposit")
        user, stdout = FakeUser(self.downloads_dir, [[(10, lands("gingham-4321.csv", "Date,Amount\n"))]]), io.StringIO()
        assist = Assist(downloads_dir=self.downloads_dir, archive_dir=self.archive_dir, open_page=user.open_page, clock=user)
        adapters = {"fake": FakeAdapter(url="https://tartan.example/export"), "gingham": FakeAdapter(url="https://gingham.example/export")}
        with contextlib.redirect_stdout(stdout):
            result = run_check_downloads([tartan("2222"), gingham], adapters, self.TODAY, assist, only="gingham")
        self.assertEqual([a.account for a in result.accounts], ["First Gingham Credit Union ••4321"])
        self.assertEqual(user.opened, ["https://gingham.example/export"])

    def test_an_adapter_no_download_account_uses_is_rejected_before_any_page_opens(self):
        with self.assertRaisesRegex(ValueError, r"^no Download Account uses adapter 'gingham'; use one of: fake$"):
            self.check([tartan("2222"), tartan("3333")], [], only="gingham")

    def test_with_no_download_accounts_there_is_nothing_to_check(self):
        with self.assertRaisesRegex(ValueError, r"^no Download Accounts to check; list them in download_accounts.toml$"):
            self.check([], [])


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
