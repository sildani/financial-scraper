import unittest

from src.sheets import export_statements_to_sheet
from tests.test_analysis import sandbox_gingham
from tests.test_plaid_transformer import NO_MOVEMENTS
from src.plaid_transformer import summarize_link


class FakeWorksheet:
    def __init__(self):
        self.rows = []

    def append_row(self, row):
        self.rows.append(row)

    def append_rows(self, rows):
        self.rows.extend(rows)


class FakeSpreadsheet:
    """gspread's Spreadsheet, keeping each tab's rows keyed by tab name without its timestamp."""

    def __init__(self):
        self.tabs = {}

    def add_worksheet(self, title, rows, cols):
        worksheet = FakeWorksheet()
        self.tabs[title.split("_")[0]] = worksheet
        return worksheet


def gingham_summaries():
    return summarize_link(sandbox_gingham(), "2026-09-17", "2026-10-17", NO_MOVEMENTS)


class PartialExportTest(unittest.TestCase):
    def test_partial_export_is_marked_incomplete_naming_the_missing_institutions(self):
        spreadsheet = FakeSpreadsheet()
        export_statements_to_sheet(gingham_summaries(), NO_MOVEMENTS, missing=["Tartan Bank"], spreadsheet=spreadsheet)
        banner = ["INCOMPLETE: missing Tartan Bank"]
        self.assertEqual(spreadsheet.tabs["Summary"].rows[0], banner)
        self.assertEqual(spreadsheet.tabs["Analysis"].rows[0], banner)

    def test_complete_export_is_not_marked(self):
        spreadsheet = FakeSpreadsheet()
        export_statements_to_sheet(gingham_summaries(), NO_MOVEMENTS, spreadsheet=spreadsheet)
        self.assertEqual(spreadsheet.tabs["Summary"].rows[0][0], "Institution")
        self.assertEqual(spreadsheet.tabs["Analysis"].rows[0][0], "--- SPEND BY CATEGORY ---")


if __name__ == "__main__":
    unittest.main()
