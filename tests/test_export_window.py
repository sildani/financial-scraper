import unittest
from datetime import date

from src.export_window import export_window, history_warning


TODAY = date(2026, 10, 18)


class ExportWindowTest(unittest.TestCase):
    def test_defaults_to_the_last_30_days(self):
        window = export_window(None, None, TODAY)
        self.assertEqual((window.start, window.end), (date(2026, 9, 18), date(2026, 10, 18)))

    def test_since_and_until_override_both_ends(self):
        window = export_window("2026-01-01", "2026-03-31", TODAY)
        self.assertEqual((window.start, window.end), (date(2026, 1, 1), date(2026, 3, 31)))

    def test_since_alone_runs_to_today(self):
        window = export_window("2026-07-01", None, TODAY)
        self.assertEqual((window.start, window.end), (date(2026, 7, 1), TODAY))

    def test_until_alone_covers_the_30_days_before_it(self):
        window = export_window(None, "2026-09-30", TODAY)
        self.assertEqual((window.start, window.end), (date(2026, 8, 31), date(2026, 9, 30)))

    def test_since_after_until_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "--since 2026-10-01 is after --until 2026-09-30"):
            export_window("2026-10-01", "2026-09-30", TODAY)

    def test_until_in_the_future_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "--until 2026-10-19 is in the future"):
            export_window(None, "2026-10-19", TODAY)

    def test_dates_must_be_iso(self):
        with self.assertRaisesRegex(ValueError, "--since must be a date like 2026-01-31, not '10/01/2026'"):
            export_window("10/01/2026", None, TODAY)


class HistoryWarningTest(unittest.TestCase):
    # Links made before Link tokens requested 730 days only have Plaid's default ~90 days

    def test_window_within_90_days_needs_no_warning(self):
        self.assertIsNone(history_warning(export_window("2026-07-20", None, TODAY), TODAY))

    def test_window_reaching_further_back_warns_to_re_link(self):
        warning = history_warning(export_window("2026-07-19", None, TODAY), TODAY)
        self.assertRegex(warning, "2026-07-19 is more than 90 days ago")
        self.assertRegex(warning, "re-link")


if __name__ == "__main__":
    unittest.main()
