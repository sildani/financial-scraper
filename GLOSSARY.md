# Glossary

**Transaction**:
A single posted movement of money on an Account. Pending activity is not a Transaction.
_Avoid_: line item, pending transaction

**Account**:
One real-world financial account (credit card, checking, loan) at an Institution, identified by Institution and last four digits.

**Liability**:
An Account where you owe money: a credit card or a loan. Positive amounts mean money out (a purchase, or debt increased).

**Deposit account**:
An Account where you hold money: checking, savings, or any other Account that isn't a Liability (e.g. investment). Positive amounts mean money in.

**Institution**:
The bank or card issuer that holds an Account.
_Avoid_: card issuer

**Category**:
One of this project's own fixed spending buckets assigned to a Transaction. Anything that doesn't clearly fit is **Other**.
_Avoid_: Plaid category (that is only a hint from a Source)

**Card Payment**:
Money paid toward a credit card from another of your Accounts. Neutral: not spending.

**Export**:
A disposable point-in-time snapshot of Transactions written to Google Sheets. Each Export is rebuilt from scratch; there is no cumulative ledger across Exports.

**Mode**:
How a run obtains Transactions: either **Plaid mode** (live from linked Institutions) or **Statement mode** (from Statement PDFs). A run uses exactly one Mode; their data is never merged. Plaid mode is the primary one; Statement mode is the fallback for Institutions Plaid cannot reach.
_Avoid_: mode for linking or re-linking; those are a run's other actions, not Modes

**Statement**:
A billing-cycle PDF issued by an Institution. Only Statement mode reads Statements.

**Transfer Out**:
Money leaving your Accounts to someone else (e.g. Zelle, Venmo, wire). Counts as spending.

**Internal Transfer**:
Money moving between two of your own Accounts. Not spending, not income.

**Linked Movement**:
A derived pairing of the two sides of a Card Payment, Internal Transfer or loan payment across two Accounts. A loan payment's Spend is counted once, on the paying side; the loan's side is only its counterpart. Every side, paired or not, is in the Category Payments & Credits, except a loan payment's paying side, which is in Mortgage & Loans. Each Account keeps its own side; the pairing is inferred, and a side with no counterpart stays unpaired and flagged.

**Displayed amount**:
A Transaction's amount as the Institution presents it, so its sign depends on the Account's kind (Liability or Deposit account).

**Spend**:
The money-out value of a Transaction that counts as spending, positive regardless of Account kind. Transfer Outs and loan payments are Spend; Card Payments, Internal Transfers and Income are not. A Refund is negative Spend. Analysis totals use Spend, never Displayed amount.

**Refund**:
Money returned by a merchant for an earlier purchase. It is negative Spend in the purchase's Category, so totals net out; it is never Income. One known exception: in Plaid mode, a Refund that Plaid files as a transfer-in can't be told apart from money in from someone else, so it is treated as Income on a Deposit account and as an unpaired Linked Movement side on a Liability.

**Income**:
Money in from outside your Accounts (wages, deposits). Reported separately and never netted against Spend.

**Export window**:
The date range of Transactions an Export covers. Defaults to today and the 30 days before it, both ends inclusive (31 days), and can be overridden per run.

**Balance**:
An Account's current balance at the time of an Export, for any Account kind.

**Amount due**:
What a Liability owes at the end of a Statement, with its due date. Only exists in Statement mode.

**Partial Export**:
An Export where one or more Links could not be fetched (Plaid mode) or one or more Statements could not be processed (Statement mode). It is allowed, but always marked incomplete and names what is missing: each Link by its Institution and Link ID, or each Statement's PDF file, since a Statement's Institution isn't known until it is processed.

**Link**:
One authorized connection to one Institution, which may cover several Accounts. A Link is Active or Needs re-link; re-linking replaces the old Link rather than adding a second one. A Link that needs re-linking causes a Partial Export.

**Link ID**:
The first 6 characters of a Link's Plaid Item ID. Output that names a Link always shows its Institution then its Link ID, e.g. "First Gingham Credit Union [a1b2c3]", so two Links at one Institution can be told apart; a Link whose Institution is unknown is named by its Link ID alone. It needs no Plaid call and reveals nothing about the Link's Accounts.
_Avoid_: naming a Link by its Institution alone, or by Account masks

**Environment**:
Where Links come from: **Sandbox** (simulated Institutions, for testing) or **Production** (your real Institutions). Every Link and Export belongs to exactly one Environment, and Environments never mix.
