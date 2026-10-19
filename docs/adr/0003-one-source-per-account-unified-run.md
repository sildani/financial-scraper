# One Source per Account, in one unified run

Supersedes ADR-0002.

Every run fetches from both Sources and writes one Export: Plaid for every Account on every Link, and Download for each Account listed in the local Download Account config. Each Account takes its Transactions from exactly one Source, so no Transaction is ever matched or deduplicated across Sources. If an Account (Institution and last four digits) arrives from both, the Plaid copy wins and the Download is skipped with a warning. Linked Movements pair across every Account in the run, whatever its Source.

ADR-0002 avoided merging because matching a PDF's Transactions to Plaid's is unreliable, and it assumed Plaid would replace PDFs. That assumption failed: some Institutions, and some Accounts at Institutions Plaid does reach, can't be linked. Keeping one Source per Account keeps ADR-0002's reasoning while letting those Accounts join the same Export. Matching whole Accounts by Institution and last four digits is enough, and Transactions never need matching. Modes are retired, and the Statement-only run goes with them.

The cost is that a run needs you at the keyboard: Downloads are assisted, so you click Download in your own browser while the app waits. A Download Account whose file doesn't cover the whole Export window, or doesn't arrive, makes a Partial Export.
