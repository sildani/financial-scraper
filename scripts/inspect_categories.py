# inspect_categories.py
from agent import process_statement_pdf

def main():
    summary = process_statement_pdf("input/apple-card-2026-08.pdf")
    
    # Group transactions by category
    by_cat = {}
    for tx in summary.transactions:
        by_cat.setdefault(tx.category.value, []).append(tx)
        
    print(f"\n--- CATEGORY BREAKDOWN ({len(summary.transactions)} total) ---")
    for cat, items in sorted(by_cat.items()):
        total = sum(i.amount for i in items)
        print(f"\n[{cat}] - {len(items)} items | Total: ${total:,.2f}")
        for item in items[:3]:  # Show top 3 examples
            print(f"  • {item.transaction_date} | {item.clean_merchant:<30} | ${item.amount:>7.2f}")
        if len(items) > 3:
            print(f"    ... and {len(items)-3} more")

if __name__ == "__main__":
    main()