import unittest

from src.config import BASE_DIR, plaid_environment

BOTH_SHEETS = {"SPREADSHEET_ID_SANDBOX": "test-sheet", "SPREADSHEET_ID_PRODUCTION": "real-sheet"}


class PlaidEnvironmentTest(unittest.TestCase):
    def test_sandbox_uses_sandbox_token_store_and_test_sheet(self):
        env = plaid_environment("sandbox", BOTH_SHEETS)
        self.assertEqual(env.name, "sandbox")
        self.assertEqual(env.token_file, BASE_DIR / ".plaid_tokens.sandbox.json")
        self.assertEqual(env.spreadsheet_id, "test-sheet")
        self.assertEqual(env.host, "https://sandbox.plaid.com")

    def test_production_uses_production_token_store(self):
        env = plaid_environment("production", BOTH_SHEETS)
        self.assertEqual(env.name, "production")
        self.assertEqual(env.token_file, BASE_DIR / ".plaid_tokens.production.json")
        self.assertEqual(env.spreadsheet_id, "real-sheet")
        self.assertEqual(env.host, "https://production.plaid.com")

    def test_missing_sheet_for_the_environment_is_rejected(self):
        # A plain SPREADSHEET_ID must not stand in: it could be the real Sheet
        env_vars = {"SPREADSHEET_ID": "real-sheet", "SPREADSHEET_ID_PRODUCTION": "real-sheet"}
        with self.assertRaisesRegex(ValueError, "SPREADSHEET_ID_SANDBOX"):
            plaid_environment("sandbox", env_vars)

    def test_unknown_environment_is_rejected(self):
        # development was retired by Plaid in June 2024
        for name in ["development", "Sandbox", "prod"]:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "PLAID_ENV"):
                plaid_environment(name, BOTH_SHEETS)

    def test_unset_environment_is_rejected(self):
        # Never fall back to Sandbox silently. A blank PLAID_ENV= in .env is unset too.
        for name in [None, ""]:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "PLAID_ENV is not set.*sandbox, production"):
                plaid_environment(name, BOTH_SHEETS)


if __name__ == "__main__":
    unittest.main()
