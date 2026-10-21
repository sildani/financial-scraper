"""Fails when a term on a `_Retired_:` line of GLOSSARY.md appears in the code or the README.

Matching ignores case, reads `_` as a space so test names count, and is on word boundaries.
Prints `file:line: term` for each hit and exits 1, or prints nothing and exits 0.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCANNED_FILES = ["main.py", "README.md"]
SCANNED_DIRS = ["src", "tests"]
# Whole phrases that contain a retired term but aren't ours
ALLOWED = [
    "update mode",  # Plaid's: Plaid Link repairing an existing Link
]


def retired_terms(glossary: str) -> list[str]:
    terms = []
    for line in glossary.splitlines():
        if line.startswith("_Retired_:"):
            listed = re.sub(r"\([^)]*\)", "", line.removeprefix("_Retired_:"))
            terms += [term.strip() for term in listed.split(",") if term.strip()]
    return terms


def phrase_pattern(phrases: list[str]) -> re.Pattern:
    # Longest first, so "Statement mode" is reported as itself rather than as "Mode"
    longest_first = sorted(phrases, key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(p) for p in longest_first) + r")\b", re.IGNORECASE)


def scanned_paths(root: Path) -> list[Path]:
    paths = [root / name for name in SCANNED_FILES if (root / name).is_file()]
    for name in SCANNED_DIRS:
        paths += [p for p in (root / name).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return sorted(paths, key=lambda p: p.relative_to(root).as_posix())


def main(root: Path = ROOT) -> int:
    terms = retired_terms((root / "GLOSSARY.md").read_text(encoding="utf-8"))
    if not terms:
        # A glossary line that drifted from the format would otherwise pass every file
        print("GLOSSARY.md has no `_Retired_: term, term (note)` line to check against", file=sys.stderr)
        return 1
    retired = phrase_pattern(terms)
    allowed = phrase_pattern(ALLOWED)
    by_lowercase = {term.lower(): term for term in terms}
    found = False
    for path in scanned_paths(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # Not text, so it can't name a term
        for number, line in enumerate(text.splitlines(), start=1):
            words = allowed.sub(" ", line.replace("_", " "))
            for match in retired.finditer(words):
                term = by_lowercase[match.group(1).lower()]
                print(f"{path.relative_to(root).as_posix()}:{number}: {term}")
                found = True
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
