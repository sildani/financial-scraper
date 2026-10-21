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
from src.llm import call_gemini_with_retries, process_statement_pdf
from src.schemas import AccountSummary
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
