# Financial Statement Agentic Scraper

An Agentic AI pipeline built with Python, PydanticAI, and Google Gemini to extract, categorize, and analyze unstructured financial statement PDFs (credit cards, bank statements, loans) into structured Google Sheets.

## Features
- **Multimodal AI Extraction:** Uses Gemini 3.6 Flash via PydanticAI to read PDF layouts natively without fragile Regex.
- **Strict Typing:** Uses Pydantic schemas to validate dates, amounts, normalized merchants, and spending categories.
- **Local MD5 Caching:** Avoids redundant API calls by caching extracted JSON outputs based on file MD5 checksums.
- **Google Sheets Integration:** Automatically outputs data across three structured sheets: *Summary*, *Detailed Spending*, and *Aggregated Spend Analysis*.
- **Plaid Integration:** Optionally connect directly to your financial institutions to fetch transaction data automatically.

## Project Structure

- `main.py`: Main entry point for running the batch statement processor.
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
   - For Google Sheets, you'll need the `SPREADSHEET_ID` and your `service_account.json`.
   - For Plaid, you'll need a `PLAID_CLIENT_ID`, `PLAID_SECRET`, and `PLAID_ENV`.

## Usage

### Manual PDF Processing

1. **Place your statement PDFs inside the `input/` directory.**
2. **Run the batch processor:**
   ```bash
   python main.py
   ```

### Automated Data Fetching with Plaid

Instead of manually downloading PDF statements, you can connect directly to your financial institutions using Plaid.

#### 1. Set up Plaid Credentials in `.env`

- Get your credentials from the [Plaid Dashboard](https://dashboard.plaid.com/team/keys).
- Add them to your `.env` file:
  ```
  PLAID_CLIENT_ID=your_plaid_client_id
  PLAID_SECRET=your_plaid_secret
  PLAID_ENV=sandbox
  ```

#### 2. Link Your Bank Account(s)

- Run the following command to start the Plaid Link flow:
  ```bash
  python main.py --plaid-link
  ```
- Follow the instructions: open the URL in your browser, complete the Link flow, and then paste the generated `public_token` back into the terminal.

#### 3. Fetch Transactions

- Once your accounts are linked, you can fetch the last 30 days of transactions by running:
  ```bash
  python main.py --fetch-transactions
  ```
