import warnings

warnings.filterwarnings("ignore", category=UserWarning)

import json
import random
import time
from functools import cache
from typing import Callable, List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.google import GoogleModel

from src.config import CACHE_DIR
from src.schemas import AccountKind, AccountSummary, SpendingCategory
from src.utils import compute_md5


@cache
def statement_agent() -> Agent[None, AccountSummary]:
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

def call_gemini_with_retries[T](call: Callable[[], T], sleep: Callable[[float], None] = time.sleep):
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
    raise AssertionError("unreachable: the last attempt returns or raises")

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


class CategoryHint(BaseModel):
    """One Transaction of a Download parsed in code, as Gemini sees it to assign its Category."""
    description: str
    vendor_category: Optional[str]  # The vendor's own category, only a hint, like a Plaid category
    is_money_out: bool


class AssignedCategory(BaseModel):
    index: int = Field(description="The Transaction's index in the list given")
    category: SpendingCategory = Field(description="The Transaction's Category, or Other when it doesn't clearly fit one")


class DownloadCategories(BaseModel):
    categories: List[AssignedCategory] = Field(description="One Category per Transaction given")


@cache
def category_agent() -> Agent[None, DownloadCategories]:
    """Gemini's Category assigner for a Download parsed in code, built on first use so importing this module needs no API key."""
    return Agent(
        GoogleModel('gemini-3.6-flash'),
        output_type=DownloadCategories,
        system_prompt=(
            "You assign each transaction from one bank or credit card account export one category from the permitted enum list. "
            "Each transaction gives its description, the bank's own category (only a hint, and often missing or coarser than ours) "
            "and whether it is money out of the account or money into it. "
            "A payment toward a credit card, a transfer between the account holder's own accounts, and a loan's own record of a payment it received "
            "are Payments & Credits. A payment made toward a mortgage or loan is Mortgage & Loans. "
            "Wages and other deposits from outside the holder's accounts are Income & Deposits. "
            "A merchant refund takes the category of the purchase it refunds. "
            "Use Other for anything that doesn't clearly fit a category."
        )
    )


def ask_gemini_for_categories(prompt: str) -> DownloadCategories:
    return category_agent().run_sync(prompt).output


def categorize_download(
    path: str, hints: List[CategoryHint], account_kind: AccountKind, force_reprocess: bool = False,
    ask: Callable[[str], DownloadCategories] = ask_gemini_for_categories,
) -> List[SpendingCategory]:
    """
    Each of hints' Category, in order, from one Gemini call per Download; Other for any Gemini leaves out.
    Cached by the file's MD5 hash, as a Statement's parse is.
    """
    if not hints:
        # A file with only pending activity has nothing to categorize, so a Gemini failure mustn't lose it
        return []
    file_hash = compute_md5(path)
    cache_file = CACHE_DIR / f"{file_hash}.categories.json"
    if cache_file.exists() and not force_reprocess:
        print(f"   ↳ [CACHE HIT] Loading cached Categories for MD5: {file_hash[:8]}...")
        return [SpendingCategory(category) for category in json.loads(cache_file.read_text())]

    print(f"   ↳ [API CALL] Asking Gemini for Categories (MD5: {file_hash[:8]})...")
    rows = [{"index": index, **hint.model_dump()} for index, hint in enumerate(hints)]
    prompt = (
        f"The account is a {account_kind} ({'a credit card or loan' if account_kind == 'liability' else 'checking, savings or similar'}). "
        f"Assign each of these transactions its category:\n{json.dumps(rows, indent=2)}"
    )
    answer = call_gemini_with_retries(lambda: ask(prompt))
    assigned = {item.index: item.category for item in answer.categories}
    categories = [assigned.get(index, SpendingCategory.OTHER) for index in range(len(hints))]
    cache_file.write_text(json.dumps([category.value for category in categories], indent=2))
    return categories
