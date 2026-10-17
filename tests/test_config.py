import unittest

from src.config import BASE_DIR, plaid_environment


class PlaidEnvironmentTest(unittest.TestCase):
    def test_sandbox_uses_sandbox_token_store(self):
        env = plaid_environment("sandbox")
        self.assertEqual(env.name, "sandbox")
        self.assertEqual(env.token_file, BASE_DIR / ".plaid_tokens.sandbox.json")

    def test_production_uses_production_token_store(self):
        env = plaid_environment("production")
        self.assertEqual(env.name, "production")
        self.assertEqual(env.token_file, BASE_DIR / ".plaid_tokens.production.json")

    def test_unknown_environment_is_rejected(self):
        # development was retired by Plaid in June 2024
        for name in ["development", "Sandbox", "prod", ""]:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "PLAID_ENV"):
                plaid_environment(name)


if __name__ == "__main__":
    unittest.main()
