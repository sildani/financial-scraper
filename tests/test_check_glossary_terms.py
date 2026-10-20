import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.check_glossary_terms import main, retired_terms

REPO_GLOSSARY = Path(__file__).resolve().parent.parent / "GLOSSARY.md"
# Made-up terms, since spelling out the real ones here would fail the check on this file
GIZMO_GLOSSARY = "**Widget**:\nA made-up term.\n_Avoid_: thingamajig\n_Retired_: Gizmo, Red gizmo (no longer used)\n"


class CheckGlossaryTermsTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / "GLOSSARY.md").write_text(GIZMO_GLOSSARY)

    def write(self, relative: str, text: str):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def check(self) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(self.root)
        return code, out.getvalue()

    def check_errors(self) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(self.root)
        return code, err.getvalue()

    def test_a_clean_tree_passes_silently(self):
        self.write("src/app.py", "x = 1\n")
        self.assertEqual(self.check(), (0, ""))

    def test_a_test_name_with_a_retired_term_fails_naming_file_line_and_term(self):
        self.write("tests/test_app.py", "import unittest\n\n    def test_x_in_red_gizmo(self):\n")
        self.assertEqual(self.check(), (1, "tests/test_app.py:3: Red gizmo\n"))

    def test_a_bare_term_in_a_comment_fails_but_a_longer_word_does_not(self):
        self.write("src/app.py", "# Gizmo is gone\n# gizmology is fine\n")
        self.assertEqual(self.check(), (1, "src/app.py:1: Gizmo\n"))

    def test_main_and_the_readme_are_scanned_but_docs_and_scripts_are_not(self):
        self.write("main.py", "gizmo = 1\n")
        self.write("README.md", "Pick a gizmo.\n")
        self.write("docs/adr/0001.md", "The gizmo was retired.\n")
        self.write("scripts/tool.py", "gizmo = 1\n")
        self.assertEqual(self.check(), (1, "README.md:1: Gizmo\nmain.py:1: Gizmo\n"))

    def test_a_new_retired_term_is_enforced_with_no_script_change(self):
        (self.root / "GLOSSARY.md").write_text(GIZMO_GLOSSARY + "_Retired_: Doohickey\n")
        self.write("src/app.py", "doohickey = 1\n")
        self.assertEqual(self.check(), (1, "src/app.py:1: Doohickey\n"))

    def test_the_real_glossary_allows_plaids_update_mode_and_unrelated_words(self):
        shutil.copy(REPO_GLOSSARY, self.root / "GLOSSARY.md")
        self.write("src/app.py", "# Opens Plaid Link in update mode\ndef test_re_link_uses_update_mode(): pass\nmodel = 1\n")
        self.assertEqual(self.check(), (0, ""))

    def test_the_real_glossary_fails_a_retired_test_name_and_a_bare_term(self):
        shutil.copy(REPO_GLOSSARY, self.root / "GLOSSARY.md")
        # Taken from the glossary, since spelling them out here would fail the check on this file
        terms = retired_terms(REPO_GLOSSARY.read_text())
        longest = max(terms, key=len)
        bare = min(terms, key=len)
        self.write("tests/test_app.py", f"    def test_x_in_{longest.lower().replace(' ', '_')}(self):\n# {bare} is gone\n")
        self.assertEqual(self.check(), (1, f"tests/test_app.py:1: {longest}\ntests/test_app.py:2: {bare}\n"))

    def test_a_glossary_with_no_retired_line_fails_rather_than_passing_silently(self):
        (self.root / "GLOSSARY.md").write_text("**Widget**:\nA made-up term.\n_Retired_ : Gizmo\n")
        code, errors = self.check_errors()
        self.assertEqual(code, 1)
        self.assertIn("_Retired_:", errors)


if __name__ == "__main__":
    unittest.main()
