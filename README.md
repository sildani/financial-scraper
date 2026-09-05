# Financial Statement Agentic Scraper

An Agentic AI pipeline built with Python, PydanticAI, and Google Gemini to extract, categorize, and analyze unstructured financial statement PDFs (credit cards, bank statements, loans) into structured Google Sheets.

## Features
- **Multimodal AI Extraction:** Uses Gemini 3.6 Flash via PydanticAI to read PDF layouts natively without fragile Regex.
- **Strict Typing:** Uses Pydantic schemas to validate dates, amounts, normalized merchants, and spending categories.
- **Local MD5 Caching:** Avoids redundant API calls by caching extracted JSON outputs based on file MD5 checksums.
- **Google Sheets Integration:** Automatically outputs data across three structured sheets: *Summary*, *Detailed Spending*, and *Aggregated Spend Analysis*.

## Setup

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/YOUR_USERNAME/financial-scraper.git](https://github.com/YOUR_USERNAME/financial-scraper.git)
   cd financial-scraper

2. **Set up virtual environment & install dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install pydantic-ai google-genai gspread pydantic python-dotenv

3. **Configure Environment Credentials:**
   - Copy .env.example to .env and supply your Gemini API key from Google AI Studio and your target Google Sheet ID.
   - Copy service_account.json.example to service_account.json and paste your Google Cloud Service Account credentials.
   - Share your target Google Sheet with the client_email listed in your service account JSON file.

## Usage

1. **Place your statement PDFs inside the input/ directory:**
   ```bash
   mkdir -p input
   # Copy your PDF files into input/

2. **Run the batch processor:**
   ```bash
   python main.py

3. **Force re-processing (bypassing local MD5 cache):**
   ```bash
   python main.py --force

