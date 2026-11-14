"""Regression tests for the dataset and the offline cleaner.

Fast by design: the dataset/cleaner tests read the CSV directly, and the two
filter tests drive the CLI once each so the exact-match guarantee is checked
end to end rather than through a copy of the logic.

    python -m pytest tests -q
"""

from __future__ import annotations

import csv
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1]
CSV_PATH = BASE / "combined_dataset.csv"
sys.path.insert(0, str(BASE))

from clean_answers import clean, safe  # noqa: E402

REQUIRED_COLUMNS = ["prompt", "question", "answer", "subject", "difficulty"]


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_schema_has_exact_required_columns(rows):
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        columns = next(csv.reader(f))
    assert columns[:5] == REQUIRED_COLUMNS


def test_no_empty_required_fields(rows):
    for column in ["question", "answer", "subject", "difficulty"]:
        empty = [i for i, r in enumerate(rows) if not r[column].strip()]
        assert not empty, f"{column} empty in {len(empty)} rows, first at {empty[:3]}"


def test_question_texts_are_unique(rows):
    counts = Counter(" ".join(r["question"].lower().split()) for r in rows)
    duplicates = {q: n for q, n in counts.items() if n > 1}
    assert not duplicates, f"{len(duplicates)} duplicate questions, e.g. {list(duplicates)[:2]}"


def test_difficulty_values_are_the_three_levels(rows):
    assert {r["difficulty"] for r in rows} == {"Easy", "Medium", "Hard"}


def test_every_subject_covers_all_three_levels(rows):
    present = {(r["subject"], r["difficulty"]) for r in rows}
    subjects = {r["subject"] for r in rows}
    missing = [(s, d) for s in subjects for d in ("Easy", "Medium", "Hard") if (s, d) not in present]
    assert not missing, f"uncovered cells: {missing}"


def test_subject_filter_is_exact_and_excludes_the_other_subject():
    """`-s Java` must never surface JavaScript rows (the bug the exact match fixed)."""
    out = subprocess.run(
        [sys.executable, "search_app.py", "-q", "closures and the event loop", "-s", "Java", "-k", "5"],
        cwd=BASE, capture_output=True, text=True, timeout=300,
    )
    assert out.returncode == 0, out.stderr[-800:]
    assert "JavaScript" not in out.stdout, "substring match leaked JavaScript into a Java query"


def test_unknown_subject_exits_with_suggestion():
    out = subprocess.run(
        [sys.executable, "search_app.py", "-q", "anything", "-s", "jav"],
        cwd=BASE, capture_output=True, text=True, timeout=300,
    )
    assert out.returncode != 0
    assert "did you mean" in (out.stdout + out.stderr).lower()


class TestCleaner:
    def test_removes_trailing_chatter(self):
        answer = (
            "A supervisor restarts failed children according to the restart policy. "
            "Feel free to ask if you have more questions about this."
        )
        cleaned = clean(answer)
        assert "Feel free" not in cleaned
        assert cleaned.endswith("policy.")
        assert safe(answer, cleaned)[0]

    def test_strips_leading_filler_and_capitalises(self):
        assert clean("Certainly. A mutex guards shared state.").startswith("A mutex")

    def test_unwraps_bold_but_never_code(self):
        assert clean("At the top sits the **root injector**, created at boot.") == (
            "At the top sits the root injector, created at boot."
        )
        exponent = "def sq(x): return x ** 2"
        assert clean(exponent) == exponent, "python exponent operator must survive"
        glob = "Add a route with the path '**' as the last entry."
        assert clean(glob) == glob, "glob pattern must survive"

    def test_guard_rejects_answers_that_become_too_thin(self):
        answer = "Yes. Feel free to ask if you have more questions about this."
        ok, why = safe(answer, clean(answer))
        assert not ok and why == "too short after cleaning"
