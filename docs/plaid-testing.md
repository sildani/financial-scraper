# Plaid Mode Acceptance Test

This is the end-to-end acceptance test for Plaid mode. It checks that an Export follows `GLOSSARY.md` and the ADRs in `docs/adr/`. It does not just check that data arrives.

- **Phase 1** runs in the **Sandbox** Environment against fixture Links with known Transactions, so every check has an exact expected value.
- **Phase 2** runs in the **Production** Environment against your real Institutions. Don't start it until every Phase 1 check passes.

Automated tests cover individual behaviors. This guide is the manual pass over the whole pipeline. Run it again after any change to Plaid mode.

## Environments

Each Environment has its own Plaid secret, token store and Google Sheet, all chosen by `PLAID_ENV`. They never mix: a Sandbox run can't see Production Links or write to the Production Sheet, and the reverse is also true.

| | Sandbox | Production |
|---|---|---|
| Purpose | Developing and testing features | Real Exports |
| Runs from | Your working checkout (any branch) | A separate checkout of `main` |
| `PLAID_ENV` | `sandbox` | `production` |
| `PLAID_SECRET` | Sandbox secret from the Plaid Keys page | Production secret from the Plaid Keys page |
| Token store | `.plaid_tokens.sandbox.json` | `.plaid_tokens.production.json` |
| Google Sheet | `SPREADSHEET_ID_SANDBOX`: a Sheet used only for testing | `SPREADSHEET_ID_PRODUCTION`: your real Sheet |

Any other `PLAID_ENV` value (including the retired `development`) must stop the run with an error.

---

## Phase 1: Sandbox

**Objective:** Prove that every Plaid mode rule in the glossary holds against known fixture data.

### Fixtures

The fixtures live in `scripts/sandbox_fixtures.json`, and `scripts/seed_sandbox.py` manages them. Amounts in the fixture file use Plaid's sign, where positive means money out for every Account kind. The expected values below use **Displayed amounts**, which follow the Account's kind (ADR-0001). Dates are relative to the day you seed. Plaid assigns random Account masks, so the steps below identify Accounts by Institution, kind and Balance.

| Link | Institution | Accounts | Created by |
|---|---|---|---|
| A | First Platypus Bank | Checking, Savings, dormant Savings, Auto loan | `seed_sandbox.py seed` |
| B | First Gingham Credit Union | Credit card | `seed_sandbox.py seed` |
| C | Tartan Bank | Checking | You, through the browser Link flow |

- [ ] **Step 1: Configure the Sandbox Environment**
  - In `.env`, set `PLAID_ENV=sandbox`, the Sandbox `PLAID_SECRET`, and `SPREADSHEET_ID_SANDBOX` to your test Sheet.
  - Delete `.plaid_tokens.sandbox.json` if it exists. Sandbox Links go stale (`ITEM_LOGIN_REQUIRED`), and leftover Links would add unknown data to the Export.

- [ ] **Step 2: Seed Links A and B**
  ```bash
  python scripts/seed_sandbox.py seed
  python scripts/seed_sandbox.py inspect
  ```
  - `inspect` prints Plaid's raw data. Confirm it shows two Links, five Accounts, and the Transactions from the fixture file.

- [ ] **Step 3: Create Link C through the browser**
  ```bash
  python scripts/seed_sandbox.py password tartan | pbcopy
  python main.py --plaid-link
  ```
  - In the browser, search for **Tartan Bank**. Sign in with username `user_custom` and paste the copied JSON as the password. Skip any phone-number prompt.
  - **Validation:**
    - [ ] The terminal reports success and saves the Link to `.plaid_tokens.sandbox.json`.
    - [ ] `python scripts/seed_sandbox.py inspect` shows three Links.

- [ ] **Step 4: Run a full Export**
  ```bash
  python main.py --fetch-transactions
  ```
  - Open the newest set of tabs in the **test** Sheet.

  **Details: Displayed amounts and Categories**

  Only Transactions inside the Export window (last 30 days) appear, and pending activity never does.

  | Institution / Account | Transaction | Displayed amount | Treated as |
  |---|---|---|---|
  | Platypus Checking | ACME Corp payroll | +2,500.00 | Income |
  | Platypus Checking | Trader Joe's | −85.00 | Spend, Groceries |
  | Platypus Checking | Payment to First Gingham card | −500.00 | Card Payment, paired |
  | Platypus Checking | Discover e-payment | −150.00 | Card Payment, **unpaired and flagged** |
  | Platypus Checking | Online transfer to savings | −300.00 | Internal Transfer, paired |
  | Platypus Checking | Zelle payment to John Doe | −80.00 | Transfer Out, Spend, Other |
  | Platypus Checking | Toyota Financial payment | −200.00 | Loan payment, Spend, Mortgage & Loans, paired |
  | Platypus Savings | Online transfer from checking | +300.00 | Internal Transfer, paired |
  | Platypus Auto loan | Payment received | −200.00 | Loan payment counterpart, paired, not Spend |
  | Gingham Credit card | Whole Foods | 60.00 | Spend, Groceries |
  | Gingham Credit card | Olive Garden | 40.00 | Spend, Restaurants & Fast Food |
  | Gingham Credit card | Delta Air Lines | 400.00 | Spend, Travel |
  | Gingham Credit card | Starbucks | 5.00 | Spend, Restaurants & Fast Food |
  | Gingham Credit card | Payment thank you | −500.00 | Card Payment, paired |
  | Gingham Credit card | Whole Foods refund | −25.00 | Refund, Groceries |
  | Gingham Credit card | ZQX Holdings | 12.00 | Spend, Other |
  | Tartan Checking | Chipotle | −14.00 | Spend, Restaurants & Fast Food |
  | Tartan Checking | Safeway | −52.00 | Spend, Groceries |

  - [ ] Every row above is present, with exactly that Displayed amount and treatment.
  - [ ] **Absent:** Shell (pending, Platypus Checking), Uber (pending, Gingham card), and Target (35 days ago, outside the Export window).
  - [ ] Exactly three Linked Movements are paired (card payment, savings transfer, loan payment), and only the Discover e-payment is flagged unpaired.

  **Analysis: Spend, never Displayed amount**

  | Spend by Category | Total |
  |---|---|
  | Travel | 400.00 |
  | Mortgage & Loans | 200.00 |
  | Groceries | 172.00 |
  | Other | 92.00 |
  | Restaurants & Fast Food | 59.00 |
  | **Total Spend** | **923.00** |

  - [ ] Category totals match exactly. Groceries is 85 + 60 − 25 + 52: the Refund nets out.
  - [ ] Income is reported separately as 2,500.00 and isn't netted against Spend.
  - [ ] Card Payments, Internal Transfers and the loan's counterpart side add nothing to any total.
  - [ ] Whole Foods totals 35.00 in the merchant breakdown.

  **Summary: Accounts and Balances**
  - [ ] Six Accounts are listed: four at First Platypus Bank, one at First Gingham, one at Tartan Bank.
  - [ ] The dormant Platypus Savings (no Transactions) is listed with Balance 5,000.00.
  - [ ] Every Account shows its Balance.
  - [ ] The Export is **not** marked incomplete.

- [ ] **Step 5: Partial Export**
  ```bash
  python scripts/seed_sandbox.py reset-login tartan
  python main.py --fetch-transactions
  ```
  - **Validation:**
    - [ ] The run finishes and doesn't crash.
    - [ ] The Export is marked incomplete and names **Tartan Bank** as missing.
    - [ ] No Tartan rows appear. Totals drop to Groceries 120.00, Restaurants & Fast Food 45.00, Total Spend 857.00, and every other Category is unchanged.
    - [ ] The terminal says Tartan Bank needs re-linking.

- [ ] **Step 6: Re-link**
  - Re-link Tartan Bank through the tool's re-link flow, using the same `user_custom` credentials as Step 3. Then run `python main.py --fetch-transactions` again.
  - **Validation:**
    - [ ] `.plaid_tokens.sandbox.json` still holds exactly three Links. The re-link replaced the old Link instead of adding a second one.
    - [ ] The Export matches Step 4 again: six Accounts, Total Spend 923.00, not marked incomplete.

- [ ] **Step 7: Environment isolation**
  - [ ] Your real (Production) Sheet got no new tabs during Phase 1.
  - [ ] `.plaid_tokens.production.json` is unchanged, or doesn't exist yet.
  - [ ] Setting `PLAID_ENV=development` (or any unknown value) stops the run with an error.

---

## Phase 2: Production

**Objective:** Confirm Plaid mode with your real Institutions, from a checkout of `main` that keeps working while you develop on other branches.

**Prerequisite:** Every Phase 1 check passes on `main`.

- [ ] **Step 1: Prepare Plaid for Production**
  - In the Plaid Dashboard, confirm your team has Production access. Limited Production is free and allows a small number of real Links.
  - Complete the application profile and company information. Many large Institutions use OAuth and won't connect until this is done.
  - Look up each Institution you plan to link in the Dashboard's institution status page, and note whether it needs OAuth registration.

- [ ] **Step 2: Create the Production checkout**
  ```bash
  git worktree add ../financial-scraper-prod main
  ```
  - Give it its own `.env` with `PLAID_ENV=production`, the Production `PLAID_SECRET`, and `SPREADSHEET_ID_PRODUCTION` to your real Sheet. Copy in `service_account.json`, and share the real Sheet with the service account.
  - Run every Phase 2 command from this checkout. Keep your development checkout on `PLAID_ENV=sandbox`.

- [ ] **Step 3: Link your real Institutions**
  ```bash
  python main.py --plaid-link
  ```
  - In the browser, find your Institution and sign in with your real credentials. Repeat for each Institution.
  - **Validation:**
    - [ ] `.plaid_tokens.production.json` holds one Link per Institution, and nothing from Sandbox.

- [ ] **Step 4: Fetch and validate real data**
  ```bash
  python main.py --fetch-transactions
  ```
  - **Validation:** open the newest tabs in your real Sheet.
    - [ ] **Details:** pick at least five recent Transactions per Account and compare them with the Institution's website. Date, description and Displayed amount (including sign) should match exactly what the Institution shows.
    - [ ] Nothing still pending at the Institution appears.
    - [ ] No Transactions older than the Export window appear.
    - [ ] Every Card Payment between two linked Accounts appears as a paired Linked Movement. Unpaired sides should only be payments to or from Accounts you haven't linked.
    - [ ] Review everything in **Other**. File an issue for each one that clearly belongs in a Category.
    - [ ] **Summary:** every linked Account is listed, and its Balance matches the Institution's website.
    - [ ] **Analysis:** total Spend for one Account roughly matches your own sum of its purchases, minus refunds, over the same window.
    - [ ] The Export is not marked incomplete. If it is, the named Institutions should be exactly the ones that need re-linking.

- [ ] **Step 5: Environment isolation**
  - [ ] No Sandbox Institution (First Platypus, First Gingham, Tartan) appears anywhere in the real Sheet.
  - [ ] The test Sheet got no new tabs during Phase 2.

---

## Troubleshooting

1. **`ITEM_LOGIN_REQUIRED` on a Sandbox Link you didn't break.** Sandbox Links go stale over time. Delete `.plaid_tokens.sandbox.json` and start Phase 1 again from Step 2.
2. **`seed_sandbox.py` refuses to run.** It only works with `PLAID_ENV=sandbox`.
3. **The fixture Transactions in `inspect` don't match the fixture file.** Plaid adds its own interest and payment rows to Sandbox student loans, mortgages, and credit accounts with subtype `credit`. Keep fixture loans and cards to the subtypes already used.
4. **An OAuth Institution fails in Production Link.** Finish the application profile and any Institution registration in the Plaid Dashboard (Phase 2, Step 1), then link again.
