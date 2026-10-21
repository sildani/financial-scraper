import tempfile
import unittest
from pathlib import Path

from src.adapters import adapters
from src.download_accounts import DownloadAccount, load_download_accounts


def load(toml: str | None, environment="production"):
    """load_download_accounts over a config file holding toml, or with no config file when toml is None."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "download_accounts.toml"
        if toml is not None:
            path.write_text(toml)
        return load_download_accounts(path, environment, adapters())


def load_error(toml: str) -> str:
    with unittest.TestCase().assertRaises(ValueError) as raised:
        load(toml)
    return str(raised.exception)


class LoadDownloadAccountsTest(unittest.TestCase):
    def test_each_account_takes_its_institution_from_its_adapter(self):
        config = load("""
            [[account]]
            adapter = "first_gingham"
            last4 = "4321"
            kind = "liability"

            [[account]]
            adapter = "first_gingham"
            last4 = "8765"
            kind = "deposit"
            balance_only = true
        """)
        self.assertEqual(config.accounts, [
            DownloadAccount(adapter="first_gingham", institution="First Gingham Credit Union", last4="4321", kind="liability"),
            DownloadAccount(adapter="first_gingham", institution="First Gingham Credit Union", last4="8765", kind="deposit", balance_only=True),
        ])

    def test_a_statement_account_names_its_own_institution(self):
        config = load("""
            [[account]]
            adapter = "statement"
            institution = "First Platypus Bank"
            last4 = "1357"
            kind = "liability"
        """)
        self.assertEqual([account.label for account in config.accounts], ["First Platypus Bank ••1357"])

    def test_the_downloads_folder_is_optional(self):
        self.assertIsNone(load("").downloads_dir)
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(load(f'downloads_dir = "{folder}"').downloads_dir, Path(folder))
        self.assertEqual(load('downloads_dir = "~"').downloads_dir, Path.home())

    def test_a_downloads_folder_that_is_not_a_folder_is_rejected(self):
        self.assertIn("downloads_dir", load_error('downloads_dir = "/no/such/folder"'))

    def test_no_config_file_means_no_download_accounts(self):
        self.assertEqual(load(None).accounts, [])

    def test_sandbox_ignores_the_config(self):
        # Download Accounts exist only in Production
        config = load('[[account]]\nadapter = "first_gingham"\nlast4 = "4321"\nkind = "liability"\n', environment="sandbox")
        self.assertEqual(config.accounts, [])

    def test_an_unknown_adapter_is_rejected_by_name(self):
        error = load_error('[[account]]\nadapter = "first_tartan"\nlast4 = "4321"\nkind = "liability"\n')
        self.assertIn("unknown adapter 'first_tartan'", error)

    def test_a_statement_account_must_name_its_institution(self):
        error = load_error('[[account]]\nadapter = "statement"\nlast4 = "1357"\nkind = "liability"\n')
        self.assertIn("adapter 'statement' needs an institution", error)

    def test_a_vendor_adapters_account_must_not_name_an_institution(self):
        error = load_error('[[account]]\nadapter = "first_gingham"\ninstitution = "Elsewhere"\nlast4 = "4321"\nkind = "liability"\n')
        self.assertIn("adapter 'first_gingham' names its own institution", error)

    def test_an_entry_with_a_bad_kind_a_missing_last4_or_an_unknown_key_is_rejected(self):
        for entry in (
            'adapter = "first_gingham"\nlast4 = "4321"\nkind = "savings"',
            'adapter = "first_gingham"\nkind = "deposit"',
            'adapter = "first_gingham"\nlast4 = "4321"\nkind = "deposit"\nbalance_onyl = true',
        ):
            with self.subTest(entry):
                self.assertIn("download_accounts.toml account 1", load_error(f"[[account]]\n{entry}\n"))


if __name__ == "__main__":
    unittest.main()
