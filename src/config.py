import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
import plaid
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Directories
BASE_DIR = Path(__file__).parent.parent
INPUT_DIR = BASE_DIR / "input"
CACHE_DIR = BASE_DIR / ".cache"

# Create cache directory if it doesn't exist
CACHE_DIR.mkdir(exist_ok=True)

# Google Sheets (the spreadsheet ID belongs to the Plaid Environment, see below)
SERVICE_ACCOUNT_FILE = BASE_DIR / "service_account.json"

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Plaid
PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")
PLAID_SECRET = os.getenv("PLAID_SECRET")

# The only valid PLAID_ENV values ('development' was retired by Plaid in June 2024)
PLAID_HOSTS = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}


@dataclass(frozen=True)
class PlaidEnvironment:
    name: str
    host: str
    token_file: Path
    spreadsheet_id: str


def plaid_environment(name: str, env: Mapping[str, str]) -> PlaidEnvironment:
    """Everything that belongs to one Environment, so Sandbox and Production never mix."""
    if name not in PLAID_HOSTS:
        raise ValueError(f"Unknown PLAID_ENV '{name}'. Use one of: {', '.join(PLAID_HOSTS)}.")
    spreadsheet_var = f"SPREADSHEET_ID_{name.upper()}"
    if not env.get(spreadsheet_var):
        raise ValueError(f"{spreadsheet_var} is not set, but PLAID_ENV is '{name}'.")
    return PlaidEnvironment(
        name=name,
        host=PLAID_HOSTS[name],
        token_file=BASE_DIR / f".plaid_tokens.{name}.json",
        spreadsheet_id=env[spreadsheet_var],
    )


@cache
def current_plaid_environment() -> PlaidEnvironment:
    """The Environment selected by PLAID_ENV. Raises ValueError if it is misconfigured."""
    return plaid_environment(os.getenv("PLAID_ENV", "sandbox"), os.environ)
