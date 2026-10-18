import contextlib
import io
import unittest

from pydantic_ai.exceptions import ModelHTTPError

from src.llm import call_gemini_with_retries


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


if __name__ == "__main__":
    unittest.main()
