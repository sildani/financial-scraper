import os
import glob
import argparse
from src.config import INPUT_DIR
from src.llm import process_statement_pdf
from src.sheets import export_statements_to_sheet

def main():
    parser = argparse.ArgumentParser(description="Financial Statement Scraper & Analyzer")
    parser.add_argument(
        "-f", "--force", 
        action="store_true", 
        help="Force re-processing via Gemini API, bypassing local cache"
    )
    args = parser.parse_args()

    pdf_files = glob.glob(os.path.join(INPUT_DIR, "*.pdf")) + glob.glob(os.path.join(INPUT_DIR, "*.PDF"))
    pdf_files = sorted(list(set(pdf_files)))

    if not pdf_files:
        print(f"No PDF statements found in '{INPUT_DIR}/'. Add your files and try again.")
        return

    print(f"Found {len(pdf_files)} PDF statement(s) in '{INPUT_DIR}/'\n")

    summaries = []
    for index, pdf_path in enumerate(pdf_files, start=1):
        filename = os.path.basename(pdf_path)
        print(f"[{index}/{len(pdf_files)}] Processing: {filename}")
        
        try:
            summary = process_statement_pdf(pdf_path, force_reprocess=args.force)
            summaries.append(summary)
            print(f"   ✓ {summary.card_issuer} ({summary.account_name}): {len(summary.transactions)} transactions")
        except Exception as e:
            print(f"   ✗ Failed to process {filename}: {e}")

    if summaries:
        print("\nExporting aggregated data to Google Sheets...")
        export_statements_to_sheet(summaries)
        print("\nBatch process complete!")

if __name__ == "__main__":
    main()
