import os
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
