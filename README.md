# Financial Statement Agentic Scraper

An Agentic AI pipeline built with Python, PydanticAI, and Google Gemini to extract, categorize, and analyze unstructured financial statement PDFs (credit cards, bank statements, loans) into structured Google Sheets.

## Features
- **Multimodal AI Extraction:** Uses Gemini 3.6 Flash via PydanticAI to read PDF layouts natively without fragile Regex.
- **Strict Typing:** Uses Pydantic schemas to validate dates, amounts, normalized merchants, and spending categories.
- **Local MD5 Caching:** Avoids redundant API calls by caching extracted JSON outputs based on file MD5 checksums.
- **Google Sheets Integration:** Automatically outputs data across three structured sheets: *Summary*, *Detailed Spending*, and *Aggregated Spend Analysis*.
- **Plaid Integration:** Connect directly to your financial institutions to fetch transaction data automatically.
- **Downloads:** Accounts Plaid can't reach are read from files you download from the Institution: a Statement PDF, or a vendor's CSV through its adapter. Both go into the same Export.

## Project Structure

- `main.py`: Main entry point: an Export of every Account by default, or `--plaid-link` / `--relink`.
- `src/`: Core application package:
  - `config.py`: Centralized configuration and environment loader.
  - `llm.py`: PydanticAI / Gemini statement extraction agent.
  - `schemas.py`: Pydantic models and Category enums.
  - `sheets.py`: Google Sheets export integration.
  - `utils.py`: Helper utilities (e.g., MD5 hashing for caching).
  - `plaid_client.py`: Client for interacting with the Plaid API.
  - `plaid_transformer.py`: Transforms Plaid data into the application's data format.
  - `download_accounts.py`: Reads the local Download Account config.
  - `downloads.py`: The adapter interface, and matching each Download Account to its file.
  - `adapters/`: One adapter per vendor (`first_gingham.py` is the worked example), plus `statement.py` for any Institution's Statement PDF.
- `scripts/`: Diagnostic and utility scripts (e.g., testing environment, category inspector, etc.).
- `input/`: Directory where Downloads (Statement PDFs, CSVs) are placed for processing.
- `download_accounts.toml.example`: An example Download Account config.
- `requirements.txt`: Project dependencies.
- `requirements-dev.txt`: Pinned versions of the check tools (ruff, pyright, pre-commit).
- `pyproject.toml`: ruff and pyright config.

## Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/YOUR_USERNAME/financial-scraper.git
   cd financial-scraper
   ```

2. **Set up virtual environment & install dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Configure Environment Credentials:**
   - Copy `.env.example` to `.env` and supply your credentials.
   - For Gemini, you'll need a `GEMINI_API_KEY`.
   - For Google Sheets, you'll need a `SPREADSHEET_ID_SANDBOX` (test Sheet) and/or `SPREADSHEET_ID_PRODUCTION` (real Sheet), and your `service_account.json`.
   - For Plaid, you'll need a `PLAID_CLIENT_ID`, `PLAID_SECRET`, and `PLAID_ENV`.

## Usage

Every run fetches from both **Sources** and writes one Export (ADR-0003): each Account on every Plaid Link, then each **Download Account** from its file in `input/`. Each Account has exactly one Source.

```bash
python main.py
```

The Export window defaults to today and the 30 days before it. Pass `--since` and/or `--until` (inclusive, `YYYY-MM-DD`) for another; it applies to every Account, whatever its Source.

### Download Accounts

Download Accounts are the Accounts Plaid can't reach. They exist only in Production; a Sandbox run ignores them.

1. **List them** in `download_accounts.toml` (gitignored). Copy `download_accounts.toml.example` to start. Each `[[account]]` has:
   - `adapter`: the vendor adapter that reads its files, such as `first_gingham`, or `statement` for any Institution's Statement PDF.
   - `institution`: only for `statement`; a vendor adapter names its own Institution.
   - `last4`: the Account's last four digits.
   - `kind`: `liability` (credit card, loan) or `deposit` (checking, savings, anything else).
   - `balance_only` (optional): not used yet.
2. **Put each Account's file in `input/`.** Each adapter recognizes its own files by their content, not their names. If several files match one Account, the last by name is used; a file no Account matches is named and left alone.
3. **Run `python main.py`.** Only Transactions inside the Export window are exported, and pending activity never is.

A Download Account with no matching file, or whose file can't be processed, makes a **Partial Export**: Summary and Analysis start with an `INCOMPLETE: missing <Institution> ••<last4>` row.

#### Statements

The `statement` adapter sends each PDF in `input/` to Gemini and caches the parse by the file's MD5, so a PDF is only sent once. Add `-f` to re-parse PDFs that are already cached. A Statement belongs to the Download Account whose last four digits end Gemini's account name.

When Gemini is overloaded (HTTP 503 or 429), each Statement is tried up to 4 times, waiting longer each time (at least 5s, 10s, then 20s). A Statement that still fails makes a Partial Export.

A Statement's Displayed amounts follow its Download Account's `kind`: checking and savings show money in as positive and money out as negative, just as your bank shows them (ADR-0001). Statements cached before Statement period dates were recorded fail to load; re-parse them once with `python main.py -f`.

### Plaid Accounts

Connect directly to your financial institutions using Plaid.

#### 1. Set up Plaid Credentials in `.env`

- Get your credentials from the [Plaid Dashboard](https://dashboard.plaid.com/team/keys).
- Add them to your `.env` file:
  ```
  PLAID_CLIENT_ID=your_plaid_client_id
  PLAID_SECRET=your_plaid_secret
  PLAID_ENV=sandbox
  ```
- `PLAID_ENV` is required and must be `sandbox` or `production`; a run with it unset stops with an error. Each Environment keeps its own Links in `.plaid_tokens.<PLAID_ENV>.json` and exports to its own Sheet (`SPREADSHEET_ID_SANDBOX` / `SPREADSHEET_ID_PRODUCTION`). `PLAID_SECRET` must belong to the same Environment. See `docs/plaid-testing.md`.

#### 2. Link Your Bank Account(s)

- Run the following command to start the Plaid Link flow:
  ```bash
  python main.py --plaid-link
  ```
- Your browser opens Plaid Link (if it doesn't, open the printed URL). Complete the flow there; the tool waits up to 5 minutes, then saves the new Link on its own.

#### 3. Fetch Transactions

- Once your accounts are linked, `python main.py` exports them with your Download Accounts.
- To export a different Export window, pass `--since` and/or `--until`:
  ```bash
  python main.py --since 2026-01-01 --until 2026-03-31
  ```
  New Links request 730 days of history. A Link made before that only has about 90 days, and `--relink` doesn't change that. To reach further back, link the Institution again with `--plaid-link`, then delete the old Link's entry from `.plaid_tokens.<PLAID_ENV>.json` so the Institution isn't fetched twice.
- If a Link fails (for example, its login expired), the run still exports the other Links. That Export is a Partial Export: Summary and Analysis start with an `INCOMPLETE: missing <Institution> [<Link ID>]` row, and the terminal names the Links that need re-linking.

#### 4. Re-link a Link

- When a Link needs re-linking, run:
  ```bash
  python main.py --relink
  ```
- This finds every Link that needs re-linking and opens Plaid Link in update mode for each one. The Link is repaired in place, so no second Link is added and the token file doesn't change.
- If one Link's session times out or Plaid returns an error, the run moves on to the next. A Link that hit a Plaid error still needs re-linking, and the summary shows the error code.
- If Plaid can't check a Link at all, the run names it by its Item ID, says it couldn't be checked, and offers the others.
- A Link whose session finished counts as re-linked even if Plaid reports another error, such as the Institution being down, which re-linking can't fix. The summary shows that error code.
- The run ends with a summary of which Links were re-linked, which still need it and which couldn't be checked, and exits with an error unless every Link was re-linked.

## Development

The pre-commit hook runs the checks listed in `.pre-commit-config.yaml`, and CI runs that same file on every pull request and every push to `main`: `ruff check`, `pyright` (basic checking), the unit tests and `scripts/check_glossary_terms.py`, which fails on any `_Retired_:` term from `GLOSSARY.md`. Their config is in `pyproject.toml`, and the tool versions are pinned in `requirements-dev.txt`.

```bash
pip install -r requirements-dev.txt
pre-commit install
pre-commit run --all-files
```

The hooks run the tools from `.venv`, so it must exist at the repo root (see Setup), but it needn't be active. The integration tests skip themselves without credentials, so CI needs none.
