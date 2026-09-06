import warnings
warnings.filterwarnings("ignore", category=UserWarning)

import json
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models.google import GoogleModel

from src.schemas import StatementSummary
from src.config import CACHE_DIR
from src.utils import compute_md5

model = GoogleModel('gemini-3.6-flash')

statement_agent = Agent(
    model,
    output_type=StatementSummary,
    system_prompt=(
        "You are an expert financial auditor and data extraction agent. "
        "Analyze the provided credit card or bank statement document thoroughly. "
        "Extract overall statement metadata (total balance, payment due date, interest charged) "
        "and every single individual line item transaction. "
        "For each transaction, provide the raw description, clean the merchant name, convert the amount, "
        "and assign the most accurate category from the permitted enum list."
    )
)

def process_statement_pdf(pdf_path: str, force_reprocess: bool = False) -> StatementSummary:
    """Processes PDF using Gemini, caching results by MD5 file hash."""
    file_hash = compute_md5(pdf_path)
    cache_file = CACHE_DIR / f"{file_hash}.json"

    # Check Cache
    if cache_file.exists() and not force_reprocess:
        print(f"   ↳ [CACHE HIT] Loading cached data for MD5: {file_hash[:8]}...")
        with open(cache_file, "r") as f:
            cached_data = json.load(f)
        return StatementSummary.model_validate(cached_data)

    # API Call on Cache Miss or Force Flag
    print(f"   ↳ [API CALL] Sending to Gemini (MD5: {file_hash[:8]})...")
    with open(pdf_path, 'rb') as f:
        pdf_bytes = f.read()

    pdf_input = BinaryContent(
        data=pdf_bytes,
        media_type='application/pdf'
    )

    result = statement_agent.run_sync([
        'Please parse this credit card or bank statement PDF and extract the structured data.',
        pdf_input
    ])
    
    summary = result.output

    # Save to Cache
    with open(cache_file, "w") as f:
        f.write(summary.model_dump_json(indent=2))

    return summary
