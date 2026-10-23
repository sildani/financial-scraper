import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.assisted_downloads import Assist, Fetch
from src.download_accounts import DownloadAccount
from src.downloads import Coverage, FetchedDownloads, download, fetch_downloads
from src.export_window import ExportWindow
from src.schemas import SpendingCategory, Transaction

WINDOW = ExportWindow(start=date(2026, 9, 18), end=date(2026, 10, 19))
START = 1_790_000_000.0  # 2026-09-21, in seconds since the epoch


class FakeAdapter:
    """First Tartan Bank's adapter: a file belongs to an Account when its first line is 'First Tartan Bank <last4>',
    and parsing gives one 20.00 Groceries purchase. Its page is url, or None like a Statement's."""
    institution = "First Tartan Bank"

    def __init__(self, url: str | None = "https://online.firsttartan.example/export"):
        self.url, self.parsed = url, []

    def fetch(self, window, account):
        return Fetch(url=self.url, instructions=f"Choose CSV, {window.start} to {window.end}")

    def recognizes(self, file, account):
        return file.read_text().splitlines()[0] == f"First Tartan Bank {account.last4}"

    def parse(self, file, account, window):
        self.parsed.append(file.name)
        purchase = Transaction(
            transaction_date=window.end.isoformat(), description="PINECONE MARKET", clean_merchant="Pinecone Market",
            amount=20.00, category=SpendingCategory.GROCERIES, spend_amount=20.00,
        )
        return download(account, Coverage(start=window.start, end=window.end), [purchase], window, 500.00)


def tartan(last4: str) -> DownloadAccount:
    return DownloadAccount(adapter="fake", institution="First Tartan Bank", last4=last4, kind="liability")


def tartan_file(last4: str) -> str:
    return f"First Tartan Bank {last4}\nDate,Amount\n"


def lands(name: str, content: str):
    """A file finishing its download into the downloads folder."""
    def action(folder: Path) -> bool:
        (folder / name).write_text(content)
        return False
    return action


def renamed(partial: str, name: str):
    """A partial download finishing, as browsers do, by renaming it."""
    def action(folder: Path) -> bool:
        (folder / partial).rename(folder / name)
        return False
    return action


def enter(folder) -> bool:
    return True


class FakeUser:
    """
    You, at the keyboard: each time a page opens, the next of plans happens, each (seconds, action) that many
    seconds later, and unprompted happens that long after START, with no page opened, as for a Statement. An
    action does something in the downloads folder, and returns True when it is pressing Enter.
    """
    def __init__(self, downloads_dir: Path, plans, unprompted=()):
        self.downloads_dir, self.plans = downloads_dir, list(plans)
        self.now, self.opened = START, []
        self.due = [(START + seconds, action) for seconds, action in unprompted]

    def open_page(self, url: str):
        self.opened.append(url)
        self.due += [(self.now + seconds, action) for seconds, action in self.plans.pop(0)]

    def time(self) -> float:
        return self.now

    def wait(self, seconds: float) -> bool:
        self.now += seconds
        happened = [event for event in self.due if event[0] <= self.now]
        self.due = [event for event in self.due if event not in happened]
        return any([action(self.downloads_dir) for _, action in happened])


class AssistedDownloadsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.downloads_dir, self.archive_dir = Path(temp.name) / "Downloads", Path(temp.name) / ".downloads"
        self.downloads_dir.mkdir()
        self.adapter = FakeAdapter()

    def fetch(self, accounts, plans, input_files=(), unprompted=()) -> tuple[FetchedDownloads, FakeUser, list[str]]:
        user = FakeUser(self.downloads_dir, plans, unprompted)
        assist = Assist(downloads_dir=self.downloads_dir, archive_dir=self.archive_dir, open_page=user.open_page, clock=user)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            fetched = fetch_downloads(accounts, list(input_files), WINDOW, {"fake": self.adapter}, assist=assist)
        return fetched, user, stdout.getvalue().splitlines()

    def test_the_page_opens_with_its_instructions_and_a_file_that_lands_is_used(self):
        fetched, user, lines = self.fetch([tartan("2222")], [[(30, lands("export.csv", tartan_file("2222")))]])
        self.assertEqual(user.opened, ["https://online.firsttartan.example/export"])
        self.assertIn("   Choose CSV, 2026-09-18 to 2026-10-19", lines)
        self.assertEqual([d.summary.account_name for d in fetched.downloads], ["••2222"])
        self.assertEqual(fetched.missing, [])

    def test_a_download_that_finishes_over_its_empty_placeholder_is_used(self):
        # Firefox saves an empty file under the final name, then moves the partial download over it
        self.fetch([tartan("2222")], [[
            (5, lands("export.csv", "")),
            (5, lands("export.csv.part", tartan_file("2222"))),
            (20, renamed("export.csv.part", "export.csv")),
        ]])
        self.assertEqual(self.adapter.parsed, ["export.csv"])

    def test_a_file_rejected_while_still_being_written_is_looked_at_again_once_it_changes(self):
        self.fetch([tartan("2222")], [[
            (5, lands("export.csv", "First Tar")),
            (10, lands("export.csv", tartan_file("2222"))),
        ]])
        self.assertEqual(self.adapter.parsed, ["export.csv"])

    def test_with_no_page_to_open_you_are_sent_to_the_institutions_website_and_the_file_is_still_taken(self):
        self.adapter = FakeAdapter(url=None)
        fetched, user, lines = self.fetch([tartan("2222")], [], unprompted=[(30, lands("statement.csv", tartan_file("2222")))])
        self.assertEqual(user.opened, [])
        self.assertIn("   Go to First Tartan Bank's website", lines)
        self.assertEqual(self.adapter.parsed, ["statement.csv"])
        self.assertEqual(fetched.missing, [])

    def test_a_matched_file_is_moved_into_the_archive_named_by_the_export_window(self):
        _, _, lines = self.fetch([tartan("2222")], [[(30, lands("export.csv", tartan_file("2222")))]])
        archived = self.archive_dir / "first-tartan-bank-2222" / "2026-09-18_to_2026-10-19.csv"
        self.assertEqual(archived.read_text(), tartan_file("2222"))
        self.assertFalse((self.downloads_dir / "export.csv").exists())
        self.assertIn("   ✓ First Tartan Bank ••2222: 1 transactions from export.csv, moved to .downloads/first-tartan-bank-2222/", lines)

    def test_an_account_whose_file_never_lands_is_missing_after_5_minutes_and_the_next_is_still_offered(self):
        fetched, user, lines = self.fetch([tartan("2222"), tartan("3333")], [[], [(10, lands("export.csv", tartan_file("3333")))]])
        self.assertEqual(len(user.opened), 2)
        self.assertEqual(user.now, START + 300 + 10)
        self.assertIn("   ✗ First Tartan Bank ••2222: no file arrived in 5 minutes", lines)
        self.assertEqual([a.last4 for a in fetched.missing], ["2222"])
        self.assertEqual([d.summary.account_name for d in fetched.downloads], ["••3333"])

    def test_a_file_already_there_before_the_page_opened_is_never_taken(self):
        (self.downloads_dir / "old.csv").write_text(tartan_file("2222"))
        fetched, _, _ = self.fetch([tartan("2222")], [[]])
        self.assertEqual([a.last4 for a in fetched.missing], ["2222"])
        self.assertEqual(self.adapter.parsed, [])
        self.assertTrue((self.downloads_dir / "old.csv").exists())

    def test_a_partial_download_is_left_until_it_finishes_and_a_file_the_adapter_rejects_is_left_where_it_is(self):
        fetched, _, _ = self.fetch([tartan("2222")], [[
            (5, lands("statement.csv", tartan_file("9999"))),
            (10, lands("export.csv.crdownload", tartan_file("2222"))),
            (20, renamed("export.csv.crdownload", "export.csv")),
        ]])
        self.assertEqual(self.adapter.parsed, ["export.csv"])
        self.assertEqual(fetched.missing, [])
        self.assertTrue((self.downloads_dir / "statement.csv").exists())

    def test_a_file_the_adapter_rejects_is_named_once_as_it_lands(self):
        _, _, lines = self.fetch([tartan("2222")], [[
            (5, lands("statement.csv", tartan_file("9999"))),
            (5, lands("statement.csv", tartan_file("9999") + "2026-10-01,20.00\n")),
            (10, lands("export.csv", tartan_file("2222"))),
        ]])
        self.assertEqual(lines.count("   ! First Tartan Bank ••2222: statement.csv landed but isn't recognized"), 1)
        self.assertEqual(self.adapter.parsed, ["export.csv"])

    def test_a_matching_file_dropped_into_input_is_used_without_opening_the_page(self):
        dropped = self.downloads_dir.parent / "tartan-2222.csv"
        dropped.write_text(tartan_file("2222"))
        fetched, user, _ = self.fetch([tartan("2222")], [], input_files=[dropped])
        self.assertEqual(user.opened, [])
        self.assertEqual(self.adapter.parsed, ["tartan-2222.csv"])
        self.assertTrue(dropped.exists())
        self.assertEqual(fetched.missing, [])

    def test_pressing_enter_skips_an_account_which_is_missing_and_the_next_is_still_offered(self):
        fetched, user, lines = self.fetch([tartan("2222"), tartan("3333")], [[(20, enter)], [(10, lands("export.csv", tartan_file("3333")))]])
        self.assertEqual(user.now, START + 20 + 10)
        self.assertIn("   ✗ First Tartan Bank ••2222: skipped", lines)
        self.assertEqual([a.last4 for a in fetched.missing], ["2222"])
        self.assertEqual([d.summary.account_name for d in fetched.downloads], ["••3333"])


if __name__ == "__main__":
    unittest.main()
