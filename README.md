# Financial Statement Agentic Scraper

An Agentic AI pipeline built with Python, PydanticAI, and Google Gemini to extract, categorize, and analyze unstructured financial statement PDFs (credit cards, bank statements, loans) into structured Google Sheets.

## Features
- **Multimodal AI Extraction:** Uses Gemini 3.6 Flash via PydanticAI to read PDF layouts natively without fragile Regex.
- **Strict Typing:** Uses Pydantic schemas to validate dates, amounts, normalized merchants, and spending categories.
- **Local MD5 Caching:** Avoids redundant API calls by caching extracted JSON outputs based on file MD5 checksums.
- **Google Sheets Integration:** Automatically outputs data across three structured sheets: *Summary*, *Detailed Spending*, and *Aggregated Spend Analysis*.
- **Plaid Integration:** Optionally connect directly to your financial institutions to fetch transaction data automatically.

## Project Structure

- `main.py`: Main entry point: a Plaid mode Export by default, or Statement mode with `--statements`.
- `src/`: Core application package:
  - `config.py`: Centralized configuration and environment loader.
  - `llm.py`: PydanticAI / Gemini statement extraction agent.
  - `schemas.py`: Pydantic models and Category enums.
  - `sheets.py`: Google Sheets export integration.
  - `utils.py`: Helper utilities (e.g., MD5 hashing for caching).
  - `plaid_client.py`: Client for interacting with the Plaid API.
  - `plaid_transformer.py`: Transforms Plaid data into the application's data format.
- `scripts/`: Diagnostic and utility scripts (e.g., testing environment, category inspector, etc.).
- `input/`: Directory where source PDF statements are placed for processing.
- `requirements.txt`: Project dependencies.

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

A run uses exactly one Mode. A bare `python main.py` runs **Plaid mode**; `--statements` runs **Statement mode** from PDFs instead.

### Statement Mode (PDFs)

1. **Place your statement PDFs inside the `input/` directory.**
2. **Run the batch processor:**
   ```bash
   python main.py --statements
   ```
   Add `-f` to re-parse PDFs that are already cached.

   When Gemini is overloaded (HTTP 503 or 429), each Statement is tried up to 4 times, waiting longer each time (at least 5s, 10s, then 20s). A Statement that still fails makes a Partial Export.

   Checking and savings Statements show money in as positive and money out as negative, just as your bank shows them (ADR-0001). Statements cached before this change don't record their Account kind and fail to load, so re-parse them once with `python main.py --statements -f`.

### Plaid Mode (default)

Instead of manually downloading PDF statements, you can connect directly to your financial institutions using Plaid.

#### 1. Set up Plaid Credentials in `.env`

- Get your credentials from the [Plaid Dashboard](https://dashboard.plaid.com/team/keys).
- Add them to your `.env` file:
  ```
  PLAID_CLIENT_ID=your_plaid_client_id
  PLAID_SECRET=your_plaid_secret
  PLAID_ENV=sandbox
  ```
- `PLAID_ENV` must be `sandbox` or `production`. Each Environment keeps its own Links in `.plaid_tokens.<PLAID_ENV>.json` and exports to its own Sheet (`SPREADSHEET_ID_SANDBOX` / `SPREADSHEET_ID_PRODUCTION`). `PLAID_SECRET` must belong to the same Environment. See `docs/plaid-testing.md`.

#### 2. Link Your Bank Account(s)

- Run the following command to start the Plaid Link flow:
  ```bash
  python main.py --plaid-link
  ```
- Your browser opens Plaid Link (if it doesn't, open the printed URL). Complete the flow there; the tool waits up to 5 minutes, then saves the new Link on its own.

#### 3. Fetch Transactions

- Once your accounts are linked, you can fetch the last 30 days of transactions by running:
  ```bash
  python main.py
  ```
- To export a different Export window, pass `--since` and/or `--until` (inclusive, `YYYY-MM-DD`). `--until` defaults to today and `--since` to 30 days before `--until`:
  ```bash
  python main.py --since 2026-01-01 --until 2026-03-31
  ```
  New Links request 730 days of history. A Link made before that only has about 90 days, and `--relink` doesn't change that. To reach further back, link the Institution again with `--plaid-link`, then delete the old Link's entry from `.plaid_tokens.<PLAID_ENV>.json` so the Institution isn't fetched twice.
- If a Link fails (for example, its login expired), the run still exports the other Links. That Export is a Partial Export: Summary and Analysis start with an `INCOMPLETE: missing <Institution>` row, and the terminal names the Links that need re-linking.

#### 4. Re-link a Link

- When a Link needs re-linking, run:
  ```bash
  python main.py --relink
  ```
- This finds every Link that needs re-linking and opens Plaid Link in update mode for each one. The Link is repaired in place, so no second Link is added and the token file doesn't change.
