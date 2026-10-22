# Glossary

_Avoid_ lists words to steer away from where they would blur a term. _Retired_ lists words that must not appear anywhere in code or docs.

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

**Source**:
Where an Account's Transactions come from: **Plaid** (every Account on every Link) or **Download**. Every Account has exactly one Source, and one run exports both Sources together (ADR-0003). If an Account arrives from both, the Plaid copy wins and the Download is skipped.
_Retired_: Mode, Plaid mode, Statement mode (a run no longer picks one)

**Download**:
A file of Transactions that you download from an Institution's own website for one Download Account: a CSV, OFX or similar export, or a Statement. The app opens the page and waits for the file in your downloads folder; you do the clicking (an assisted Download). A file dropped into `input/` by hand counts too.
_Avoid_: scrape, statement (for a file that isn't a Statement)

**Download Account**:
An Account whose Source is Download, listed in the local Download Account config. Plaid Accounts are never listed there. Download Accounts exist only in Production.

**Downloads folder**:
Where your browser saves files: `downloads_dir` in the Download Account config, `~/Downloads` by default. An assisted Download is taken from it only if it is a finished file that lands after that Account's page opened and its adapter recognizes it; every other file is left where it is.

**Download archive**:
The gitignored `.downloads/` folder that each assisted Download is moved into once taken, one folder per Download Account, each file named by its Export window. Files older than 90 days are deleted at the start of each Export.
_Avoid_: cache (that holds Gemini's parses of Statements)

**Balance-only**:
A Download Account that contributes a Balance and no Transactions, such as a closed card that still owes money. Coverage doesn't apply to it.

**Coverage**:
The dates a Download Account's files actually span within the Export window. A Plaid Account always covers the whole window. A Download Account may stop short, for example at its latest Statement's close.

**Statement**:
A billing-cycle PDF issued by an Institution. A Statement is one kind of Download.

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
Money returned by a merchant for an earlier purchase. It is negative Spend in the purchase's Category, so totals net out; it is never Income. One known exception: from Plaid, a Refund that Plaid files as a transfer-in can't be told apart from money in from someone else, so it is treated as Income on a Deposit account and as an unpaired Linked Movement side on a Liability.

**Income**:
Money in from outside your Accounts (wages, deposits). Reported separately and never netted against Spend.

**Export window**:
The date range of Transactions an Export covers. Defaults to today and the 30 days before it, both ends inclusive (31 days), and can be overridden per run. It applies to every Account, whatever its Source: a Download's Transactions outside it are dropped.

**Balance**:
An Account's balance, for any Account kind, with the date it is as of: the time of the Export for Plaid, or the last date its Download's file spans, which for a Statement is its close. Blank when a Download doesn't provide one.

**Amount due**:
What a Liability owes at the end of a Statement, with its due date. Only Statements provide it.

**Partial Export**:
An Export where something is missing: a Link that could not be fetched, a Download Account whose Download didn't arrive or couldn't be processed (or whose Balance couldn't be obtained, if it is Balance-only), or a Download Account whose Coverage stops short of the Export window. It is allowed, but always marked incomplete and names what is missing: each Link by its Institution and Link ID, and each Download Account by its Institution and last four digits, with the date its Coverage reaches if it stops short.

**Link**:
One authorized connection to one Institution, which may cover several Accounts. A Link is Active or Needs re-link; re-linking replaces the old Link rather than adding a second one. A Link that needs re-linking causes a Partial Export.

**Link ID**:
The first 6 characters of a Link's Plaid Item ID. Output that names a Link always shows its Institution then its Link ID, e.g. "First Gingham Credit Union [a1b2c3]", so two Links at one Institution can be told apart; a Link whose Institution is unknown is named by its Link ID alone. It needs no Plaid call and reveals nothing about the Link's Accounts.
_Avoid_: naming a Link by its Institution alone, or by Account masks

**Environment**:
Where Links come from: **Sandbox** (simulated Institutions, for testing) or **Production** (your real Institutions). Every Link and Export belongs to exactly one Environment, and Environments never mix. Download Accounts belong to Production only.
