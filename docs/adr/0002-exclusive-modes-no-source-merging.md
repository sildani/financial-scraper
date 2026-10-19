# One Mode per run; Plaid and Statement data are never merged

Superseded by ADR-0003.

A run uses either Plaid mode or Statement mode, never both, and their Transactions are never combined or deduplicated against each other. Plaid mode is the primary one; Statement mode remains as a fallback for Institutions Plaid cannot reach.

We rejected merging the two Sources because it needs cross-Source identity matching of Accounts and Transactions (descriptions, dates and amounts differ between a PDF and Plaid), and Plaid is intended to replace manual PDF downloads anyway. The cost is that an Institution only available as PDFs can't appear in the same Export as Plaid-linked ones.
