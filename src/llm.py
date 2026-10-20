import warnings
warnings.filterwarnings("ignore", category=UserWarning)

import json
import random
import time
from functools import cache
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.google import GoogleModel

from src.schemas import AccountSummary
from src.config import CACHE_DIR
from src.utils import compute_md5

@cache
def statement_agent() -> Agent:
    """Gemini's Statement parser, built on first use so importing this module needs no API key."""
    return Agent(
        GoogleModel('gemini-3.6-flash'),
        output_type=AccountSummary,
        system_prompt=(
            "You are an expert financial auditor and data extraction agent. "
            "Analyze the provided credit card or bank statement document thoroughly. "
            "Extract overall statement metadata (account kind, balance, amount due and due date for credit cards or loans, interest charged) "
            "and every single individual transaction. "
            "For each transaction, provide the raw description, clean the merchant name, convert the amount "
            "(positive for money out, on every kind of account), "
            "and assign the most accurate category from the permitted enum list."
        )
    )

GEMINI_ATTEMPTS = 4
GEMINI_RETRY_BASE_DELAY_S = 5
GEMINI_UNAVAILABLE_STATUS_CODES = (429, 503)  # Too many requests, or the model is overloaded

def call_gemini_with_retries(call, sleep=time.sleep):
    """call(), retried with growing, jittered delays while Gemini is unavailable."""
    for attempt in range(1, GEMINI_ATTEMPTS + 1):
        try:
            return call()
        except ModelHTTPError as e:
            if e.status_code not in GEMINI_UNAVAILABLE_STATUS_CODES or attempt == GEMINI_ATTEMPTS:
                raise
            delay = GEMINI_RETRY_BASE_DELAY_S * 2 ** (attempt - 1) * random.uniform(1, 1.5)
            print(f"   ↳ [RETRY] Gemini unavailable ({e.status_code}); retrying in {delay:.0f}s (attempt {attempt + 1}/{GEMINI_ATTEMPTS})")
            sleep(delay)

def process_statement_pdf(pdf_path: str, force_reprocess: bool = False) -> AccountSummary:
    """Gemini's parse of a Statement PDF, cached by MD5 file hash; its amounts are positive for money out."""
    file_hash = compute_md5(pdf_path)
    cache_file = CACHE_DIR / f"{file_hash}.json"

    # Check Cache
    if cache_file.exists() and not force_reprocess:
        print(f"   ↳ [CACHE HIT] Loading cached data for MD5: {file_hash[:8]}...")
        with open(cache_file, "r") as f:
            cached_data = json.load(f)
        return AccountSummary.model_validate(cached_data)

    # API Call on Cache Miss or Force Flag
    print(f"   ↳ [API CALL] Sending to Gemini (MD5: {file_hash[:8]})...")
    with open(pdf_path, 'rb') as f:
        pdf_bytes = f.read()

    pdf_input = BinaryContent(
        data=pdf_bytes,
        media_type='application/pdf'
    )

    result = call_gemini_with_retries(lambda: statement_agent().run_sync([
        'Please parse this credit card or bank statement PDF and extract the structured data.',
        pdf_input
    ]))
    
    summary = result.output

    # Save to Cache
    with open(cache_file, "w") as f:
        f.write(summary.model_dump_json(indent=2))

    return summary
