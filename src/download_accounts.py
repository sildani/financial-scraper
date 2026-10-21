import re
import tomllib
from pathlib import Path
from typing import List

from pydantic import BaseModel, ConfigDict, ValidationError

from src.config import DEFAULT_DOWNLOADS_DIR
from src.schemas import AccountKind


class DownloadAccount(BaseModel):
    """One entry of the local Download Account config, with its Institution resolved."""
    model_config = ConfigDict(extra="forbid")  # A misspelt key, such as balance_onyl, is an error, not ignored

    adapter: str
    institution: str
    last4: str
    kind: AccountKind
    balance_only: bool = False

    @property
    def account_name(self) -> str:
        return f"••{self.last4}"

    @property
    def label(self) -> str:
        """The Account as output names it: its Institution and last four digits."""
        return f"{self.institution} {self.account_name}"

    @property
    def slug(self) -> str:
        """The Account as a folder name, e.g. first-gingham-credit-union-4321."""
        return re.sub(r"[^a-z0-9]+", "-", f"{self.institution} {self.last4}".lower()).strip("-")


class DownloadConfig(BaseModel):
    accounts: List[DownloadAccount] = []
    downloads_dir: Path  # Where assisted Downloads land


def load_download_accounts(path: Path, environment: str, adapters: dict, default_downloads_dir: Path = DEFAULT_DOWNLOADS_DIR) -> DownloadConfig:
    """
    The Download Accounts in the config at path, each with its Institution from its adapter, or from the
    entry itself for an adapter that has none (the Statement adapter), and the downloads folder: the
    config's downloads_dir, or else default_downloads_dir. Raises ValueError for a bad entry, or for a
    downloads folder that doesn't exist when there are Download Accounts to wait for.
    """
    # Download Accounts exist only in Production, and a missing config means there are none
    if environment != "production" or not path.exists():
        return DownloadConfig(downloads_dir=default_downloads_dir)
    with open(path, "rb") as f:
        try:
            config = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"{path.name}: {e}") from None

    accounts = []
    for number, entry in enumerate(config.get("account", []), start=1):
        where = f"{path.name} account {number}"
        name = entry.get("adapter")
        if name not in adapters:
            raise ValueError(f"{where}: unknown adapter '{name}'. Use one of: {', '.join(adapters)}.")
        institution = adapters[name].institution
        if institution is None and "institution" not in entry:
            raise ValueError(f"{where}: adapter '{name}' needs an institution")
        if institution is not None and "institution" in entry:
            raise ValueError(f"{where}: adapter '{name}' names its own institution; remove institution")
        try:
            accounts.append(DownloadAccount(**{"institution": institution, **entry}))
        except ValidationError as e:
            raise ValueError(f"{where}: {e}") from None
    configured = config.get("downloads_dir")
    downloads_dir = Path(configured).expanduser() if configured else default_downloads_dir
    if accounts and not downloads_dir.is_dir():
        raise ValueError(f"the downloads folder {downloads_dir} doesn't exist; set downloads_dir in {path.name}")
    return DownloadConfig(accounts=accounts, downloads_dir=downloads_dir)
