import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pydantic_ai.exceptions import ModelHTTPError, UserError

from src import llm
from src.llm import (
    AssignedCategory,
    CategoryHint,
    DownloadCategories,
    call_gemini_with_retries,
    categorize_download,
    process_statement_pdf,
)
from src.schemas import AccountSummary, SpendingCategory
from src.utils import compute_md5


def flaky(*outcomes):
    """A Gemini call that raises or returns each outcome in turn, counting its calls."""
    def call():
        call.count += 1
        outcome = outcomes[call.count - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    call.count = 0
    return call


def http_error(status_code=503):
    return ModelHTTPError(status_code, "gemini-3.6-flash", {"error": {"status": "UNAVAILABLE"}})


def retried(call):
    """call_gemini_with_retries(call) without sleeping or printing; returns its result or error, and the delays it waited."""
    delays = []
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            return call_gemini_with_retries(call, sleep=delays.append), delays
        except Exception as e:
            return e, delays


class GeminiRetryTest(unittest.TestCase):
    def test_an_unavailable_model_is_retried_until_it_answers(self):
        for status_code in (503, 429):
            with self.subTest(status_code):
                call = flaky(http_error(status_code), http_error(status_code), "parsed")
                result, delays = retried(call)
                self.assertEqual((result, call.count, len(delays)), ("parsed", 3, 2))

    def test_other_errors_are_raised_without_retrying(self):
        for error in (http_error(status_code=400), http_error(status_code=401), ValueError("bad output")):
            with self.subTest(error):
                call = flaky(error, "parsed")
                self.assertIs(retried(call)[0], error)
                self.assertEqual(call.count, 1)

    def test_a_model_that_stays_unavailable_raises_its_last_error_after_four_attempts(self):
        errors = [http_error() for _ in range(4)]
        call = flaky(*errors)
        error, delays = retried(call)
        self.assertIs(error, errors[-1])
        self.assertEqual((call.count, len(delays)), (4, 3))


GEMINI_KEY_VARIABLES = ("GOOGLE_API_KEY", "GEMINI_API_KEY")


def environ_without_gemini_key():
    return {name: value for name, value in os.environ.items() if name not in GEMINI_KEY_VARIABLES}


class ImportWithoutGeminiKeyTest(unittest.TestCase):
    def test_the_gemini_module_and_statement_adapter_import_without_a_key(self):
        # load_dotenv() is stubbed out so a .env here or in a parent directory can't supply a key.
        imported = subprocess.run(
            [sys.executable, "-B", "-c",
             "import dotenv; dotenv.load_dotenv = lambda *args, **kwargs: False; import src.llm, src.adapters.statement"],
            cwd=Path(__file__).parents[1], env=environ_without_gemini_key(), capture_output=True, text=True,
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)


class ParseWithoutGeminiKeyTest(unittest.TestCase):
    def setUp(self):
        llm.statement_agent.cache_clear()
        self.addCleanup(llm.statement_agent.cache_clear)
        self.enterContext(mock.patch.dict(os.environ, environ_without_gemini_key(), clear=True))
        self.cache_dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(llm, "CACHE_DIR", self.cache_dir))
        self.pdf = self.cache_dir / "statement.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 a Statement")

    def parsed(self):
        """process_statement_pdf(self.pdf), without printing."""
        with contextlib.redirect_stdout(io.StringIO()):
            return process_statement_pdf(str(self.pdf))

    def test_a_cached_statement_is_parsed_without_a_key(self):
        cached = AccountSummary(
            institution="Bank", account_name="Checking - 0000", period="Aug 01, 2026 - Aug 31, 2026",
            account_kind="deposit", balance=100.0, transactions=[],
        )
        (self.cache_dir / f"{compute_md5(self.pdf)}.json").write_text(cached.model_dump_json())
        self.assertEqual(self.parsed(), cached)

    def test_an_uncached_statement_without_a_key_fails_asking_for_one(self):
        with self.assertRaisesRegex(UserError, "GOOGLE_API_KEY"):
            self.parsed()
        self.assertEqual([file.name for file in self.cache_dir.iterdir()], ["statement.pdf"])


HINTS = [
    CategoryHint(description="PINECONE MARKET #12", vendor_category="Groceries", is_money_out=True),
    CategoryHint(description="PAYMENT THANK YOU", vendor_category="Payment", is_money_out=False),
    CategoryHint(description="KNITTING CIRCLE SUPPLY", vendor_category=None, is_money_out=True),
]


class FakeGemini:
    """Gemini's answer to a Download's Categories prompt, recording each prompt it's asked."""
    def __init__(self, *assigned: tuple[int, SpendingCategory]):
        self.answer = DownloadCategories(categories=[AssignedCategory(index=i, category=c) for i, c in assigned])
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> DownloadCategories:
        self.prompts.append(prompt)
        return self.answer


class CategorizeDownloadTest(unittest.TestCase):
    def setUp(self):
        self.cache_dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(llm, "CACHE_DIR", self.cache_dir))
        self.csv = self.cache_dir / "download.csv"
        self.csv.write_text("Date,Description,Category,Amount\n")
        self.gemini = FakeGemini(
            (2, SpendingCategory.OTHER), (0, SpendingCategory.GROCERIES), (1, SpendingCategory.PAYMENTS_CREDITS),
        )

    def categorized(self, gemini=None, force_reprocess=False):
        """categorize_download over self.csv's HINTS, without printing."""
        with contextlib.redirect_stdout(io.StringIO()):
            return categorize_download(str(self.csv), HINTS, "liability", force_reprocess=force_reprocess, ask=gemini or self.gemini)

    def test_one_gemini_call_per_download_gives_each_row_its_category_in_order(self):
        self.assertEqual(self.categorized(), [
            SpendingCategory.GROCERIES, SpendingCategory.PAYMENTS_CREDITS, SpendingCategory.OTHER,
        ])
        self.assertEqual(len(self.gemini.prompts), 1)

    def test_the_prompt_carries_each_rows_description_category_hint_and_amount_sign_and_the_account_kind(self):
        self.categorized()
        prompt = self.gemini.prompts[0]
        for expected in (
            '"description": "PINECONE MARKET #12"', '"vendor_category": "Groceries"', '"is_money_out": true',
            '"description": "PAYMENT THANK YOU"', '"is_money_out": false', '"vendor_category": null', "liability",
        ):
            self.assertIn(expected, prompt)

    def test_a_transaction_gemini_leaves_out_is_other(self):
        partial = FakeGemini((0, SpendingCategory.GROCERIES), (7, SpendingCategory.TRAVEL))
        self.assertEqual(self.categorized(partial), [SpendingCategory.GROCERIES, SpendingCategory.OTHER, SpendingCategory.OTHER])

    def test_an_unavailable_gemini_is_retried(self):
        answers = flaky(http_error(), self.gemini.answer)
        with mock.patch.object(llm, "GEMINI_RETRY_BASE_DELAY_S", 0):
            self.assertEqual(self.categorized(lambda prompt: answers())[0], SpendingCategory.GROCERIES)
        self.assertEqual(answers.count, 2)

    def test_a_download_with_no_transactions_asks_gemini_nothing(self):
        gemini = FakeGemini()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(categorize_download(str(self.csv), [], "deposit", ask=gemini), [])
        self.assertEqual(gemini.prompts, [])

    def test_a_second_run_over_the_same_file_makes_no_gemini_call(self):
        first = self.categorized()
        again = FakeGemini()
        self.assertEqual(self.categorized(again), first)
        self.assertEqual(again.prompts, [])

    def test_force_asks_gemini_again_and_caches_its_new_answer(self):
        self.categorized()
        changed = FakeGemini((0, SpendingCategory.RESTAURANTS_FAST_FOOD))
        self.assertEqual(self.categorized(changed, force_reprocess=True)[0], SpendingCategory.RESTAURANTS_FAST_FOOD)
        self.assertEqual(len(changed.prompts), 1)
        self.assertEqual(self.categorized(FakeGemini())[0], SpendingCategory.RESTAURANTS_FAST_FOOD)

    def test_a_changed_file_is_asked_about_again(self):
        self.categorized()
        self.csv.write_text("Date,Description,Category,Amount\n2026-10-01,PINECONE MARKET #12,Groceries,-1.00\n")
        changed = FakeGemini()
        self.categorized(changed)
        self.assertEqual(len(changed.prompts), 1)


class StatementSchemaTest(unittest.TestCase):
    """AccountSummary is Gemini's output schema for a Statement, so each field it gains is one more Gemini is asked to fill."""

    WRAP_EXPORT_ONLY_FIELDS = (
        "a field the Export sets, not Gemini, must be wrapped in SkipJsonSchema; "
        "add it to the pinned fields in StatementSchemaTest only if Gemini should read it from a Statement"
    )

    def test_gemini_is_asked_only_for_the_statement_fields(self):
        schema = AccountSummary.model_json_schema()
        self.assertEqual(set(schema["properties"]), {
            "institution", "account_name", "period", "period_start", "period_end", "account_kind",
            "balance", "amount_due", "due_date", "interest_charged", "transactions",
        }, self.WRAP_EXPORT_ONLY_FIELDS)
        self.assertEqual(set(schema["$defs"]["ParsedTransaction"]["properties"]), {
            "transaction_date", "description", "clean_merchant", "amount", "category",
        }, self.WRAP_EXPORT_ONLY_FIELDS)


if __name__ == "__main__":
    unittest.main()
