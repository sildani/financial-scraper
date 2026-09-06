from agent import process_statement_pdf
from sheets import export_statements_to_sheet

def main():
    print("Processing statement...")
    summary = process_statement_pdf("input/apple-card-2026-08.pdf")
    
    print("Exporting to Google Sheets...")
    export_statements_to_sheet([summary])

if __name__ == "__main__":
    main()