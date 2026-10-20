import tomllib
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, ValidationError

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


class DownloadConfig(BaseModel):
    accounts: List[DownloadAccount] = []
    downloads_dir: Optional[Path] = None  # Where assisted Downloads land; None for the default


def load_download_accounts(path: Path, environment: str, adapters: dict) -> DownloadConfig:
    """
    The Download Accounts in the config at path, each with its Institution from its adapter, or from the
    entry itself for an adapter that has none (the Statement adapter). Raises ValueError for a bad entry.
    """
    # Download Accounts exist only in Production, and a missing config means there are none
    if environment != "production" or not path.exists():
        return DownloadConfig()
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
    downloads_dir = config.get("downloads_dir")
    return DownloadConfig(accounts=accounts, downloads_dir=Path(downloads_dir).expanduser() if downloads_dir else None)
