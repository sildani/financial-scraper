import os
import sys
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Directories
BASE_DIR = Path(__file__).parent.parent
INPUT_DIR = BASE_DIR / "input"
CACHE_DIR = BASE_DIR / ".cache"

# Create cache directory if it doesn't exist
CACHE_DIR.mkdir(exist_ok=True)

# Google Sheets
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_FILE = BASE_DIR / "service_account.json"

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Plaid
PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")
PLAID_SECRET = os.getenv("PLAID_SECRET")
PLAID_ENV = os.getenv("PLAID_ENV", "sandbox")


@dataclass(frozen=True)
class PlaidEnvironment:
    name: str
    token_file: Path


PLAID_ENVIRONMENTS = ("sandbox", "production")


def plaid_environment(name: str) -> PlaidEnvironment:
    if name not in PLAID_ENVIRONMENTS:
        raise ValueError(f"Unknown PLAID_ENV '{name}'. Use one of: {', '.join(PLAID_ENVIRONMENTS)}.")
    return PlaidEnvironment(name=name, token_file=BASE_DIR / f".plaid_tokens.{name}.json")


# PLAID_SECRET and SPREADSHEET_ID in .env must belong to the same Environment as PLAID_ENV
try:
    PLAID_ENVIRONMENT = plaid_environment(PLAID_ENV)
except ValueError as e:
    sys.exit(f"Error: {e}")
