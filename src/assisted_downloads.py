import select
import shutil
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

from pydantic import BaseModel

from src.download_accounts import DownloadAccount
from src.export_window import ExportWindow

WAIT_S = 300
POLL_S = 1
ARCHIVE_DAYS = 90
PARTIAL_SUFFIXES = (".crdownload", ".download", ".part")  # Chrome's, Safari's and Firefox's


class Fetch(BaseModel):
    """Where to get an Account's Download, and what to pick there for an Export window."""
    url: Optional[str]  # None when the adapter can't know the page, as for any Institution's Statement
    instructions: str


class Fetches(Protocol):
    def fetch(self, window: ExportWindow) -> Fetch: ...


class Recognizes(Protocol):
    def recognizes(self, file: Path, account: DownloadAccount) -> bool:
        """Whether file belongs to account, decided by its content, not its name."""
        ...


class Watched(Fetches, Recognizes, Protocol):
    pass


def recognized(adapter: Recognizes, file: Path, account: DownloadAccount) -> bool:
    """Whether adapter recognizes file as account's; a file it can't read is not, and is named."""
    try:
        return adapter.recognizes(file, account)
    except Exception as e:
        print(f"   ! {account.label}: couldn't read {file.name}: {e}")
        return False


class Now(Protocol):
    def time(self) -> float: ...


class Wait(Protocol):
    def wait(self, seconds: float) -> bool:
        """Waits seconds; True if Enter was pressed meanwhile, which skips the Account."""
        ...


class Clock(Now, Wait, Protocol):
    pass


class TerminalClock:
    def time(self) -> float:
        return time.time()

    def wait(self, seconds: float) -> bool:
        # Without a terminal there's no Enter to press, and stdin at its end would read as ready at once
        if not sys.stdin.isatty():
            time.sleep(seconds)
            return False
        ready, _, _ = select.select([sys.stdin], [], [], seconds)
        if ready:
            sys.stdin.readline()
        return bool(ready)


@dataclass
class Assist:
    """How assisted Downloads reach you: the page opens in your browser, and the file lands in downloads_dir."""
    downloads_dir: Path
    archive_dir: Path
    open_page: Callable[[str], object] = webbrowser.open
    clock: Clock = TerminalClock()


def watch_downloads(account: DownloadAccount, adapter: Watched, window: ExportWindow, assist: Assist) -> Optional[Path]:
    """Opens account's page and waits for a file its adapter recognizes to land in the downloads folder."""
    fetch = adapter.fetch(window)
    # Only a file that lands after the page opens can be this Download
    before = set(assist.downloads_dir.iterdir())
    print(f"\n--- Download {account.label} ---")
    if fetch.url:
        print(f"   Opening {fetch.url}")
        assist.open_page(fetch.url)
    else:
        print(f"   Go to {account.institution}'s website")
    print(f"   {fetch.instructions}")
    print(f"   Waiting up to {WAIT_S // 60} minutes for it in {assist.downloads_dir}; press Enter to skip...")

    # Each file's size and modification time when it was last looked at: one still being written is looked at again
    looked_at: dict[Path, tuple[int, float]] = {}
    deadline = assist.clock.time() + WAIT_S
    while True:
        for file in sorted(set(assist.downloads_dir.iterdir()) - before):
            # A partial download is renamed when it finishes, and Firefox's empty placeholder is then replaced,
            # so either is looked at again once it has
            if file.suffix in PARTIAL_SUFFIXES or file.name.startswith(".") or not file.is_file():
                continue
            stat = file.stat()
            if not stat.st_size or looked_at.get(file) == (stat.st_size, stat.st_mtime):
                continue
            looked_at[file] = (stat.st_size, stat.st_mtime)
            if recognized(adapter, file, account):
                return file
        if assist.clock.time() >= deadline:
            print(f"   ✗ {account.label}: no file arrived in {WAIT_S // 60} minutes")
            return None
        if assist.clock.wait(POLL_S):
            print(f"   ✗ {account.label}: skipped")
            return None


def prune_archive(assist: Assist):
    """Deletes archived Downloads older than ARCHIVE_DAYS, by when they were downloaded."""
    oldest = assist.clock.time() - ARCHIVE_DAYS * 24 * 60 * 60
    for file in assist.archive_dir.glob("*/*"):
        if file.is_file() and file.stat().st_mtime < oldest:
            file.unlink()


def archive_download(file: Path, account: DownloadAccount, window: ExportWindow, assist: Assist) -> str:
    """Moves an assisted Download into the archive, named by its Account and Export window; returns where, as output names it."""
    folder = assist.archive_dir / account.slug
    folder.mkdir(parents=True, exist_ok=True)
    shutil.move(file, folder / f"{window.start.isoformat()}_to_{window.end.isoformat()}{file.suffix}")
    return f"{assist.archive_dir.name}/{account.slug}/"
