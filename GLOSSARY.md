# Glossary

**Transaction**:
A single posted movement of money on an Account. Pending activity is not a Transaction.
_Avoid_: line item, pending transaction

**Account**:
One real-world financial account (credit card, checking, loan) at an Institution, identified by Institution and last four digits.

**Liability**:
An Account where you owe money: a credit card or a loan. Positive amounts mean money out (a purchase, or debt increased).

**Deposit account**:
An Account where you hold money: checking or savings. Positive amounts mean money in.

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

**Statement**:
A billing-cycle PDF issued by an Institution. Only Statement mode reads Statements.

**Transfer Out**:
Money leaving your Accounts to someone else (e.g. Zelle, Venmo, wire). Counts as spending.

**Internal Transfer**:
Money moving between two of your own Accounts. Not spending, not income.

**Linked Movement**:
A derived pairing of the two sides of a Card Payment or Internal Transfer across two Accounts. Each Account keeps its own side; the pairing is inferred, and a side with no counterpart stays unpaired and flagged.

**Displayed amount**:
A Transaction's amount as the Institution presents it, so its sign depends on the Account's kind (Liability or Deposit account).

**Spend**:
The money-out value of a Transaction that counts as spending, positive regardless of Account kind. Transfer Outs and loan payments are Spend; Card Payments, Internal Transfers and Income are not. Analysis totals use Spend, never Displayed amount.

**Income**:
Money in from outside your Accounts (wages, deposits). Reported separately and never netted against Spend.

**Export window**:
The date range of Transactions an Export covers. Defaults to the last 30 days and can be overridden per run.

**Balance**:
An Account's current balance at the time of an Export, for any Account kind.

**Amount due**:
What a Liability owes at the end of a Statement, with its due date. Only exists in Statement mode.

**Partial Export**:
An Export where one or more Institutions could not be fetched. It is allowed, but always marked incomplete and names the missing Institutions.

**Link**:
One authorized connection to one Institution, which may cover several Accounts. A Link is Active or Needs re-link; re-linking replaces the old Link rather than adding a second one. A Link that needs re-linking causes a Partial Export.
