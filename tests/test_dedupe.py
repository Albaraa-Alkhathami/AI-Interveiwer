"""The mechanical dedupe pass: selection rules, coverage guard, and the file write."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import dedupe_answers as dd  # noqa: E402


def rows_of(*specs):
    return [{"prompt": "", "question": q, "answer": a, "subject": s,
             "difficulty": d, "data_flag": ""} for q, a, s, d in specs]


def test_longest_specific_body_is_kept_and_ties_go_to_dataset_order():
    rows = rows_of(("short?", "x" * 40, "Java", "Easy"),
                   ("longer?", "x" * 120, "Java", "Easy"))
    removed, protected = dd.plan_removals(rows, [[0, 1]], [0, 1],
                                          [rows[0]["answer"], rows[1]["answer"]],
                                          scope="answer")
    assert removed == [0], "the richer body must survive"
    assert protected == []

    # same cell, so the coverage guard cannot protect the second row
    tied = rows_of(("first?", "x" * 100, "Java", "Easy"),
                   ("second?", "x" * 100, "Java", "Easy"))
    removed, _ = dd.plan_removals(tied, [[0, 1]], [0, 1],
                                  [tied[0]["answer"], tied[1]["answer"]], scope="answer")
    assert removed == [1], "equal bodies keep the earlier row"


def test_question_scope_will_not_delete_a_different_question():
    rows = rows_of(("Why are cookies split by party?", "x" * 100, "Angular", "Easy"),
                   ("Why is entryComponents unnecessary?", "x" * 100, "Angular", "Easy"))
    qvectors = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)  # cosine 0
    removed, protected = dd.plan_removals(rows, [[0, 1]], [0, 1],
                                          [rows[0]["answer"], rows[1]["answer"]],
                                          scope="question", qvectors=qvectors)
    assert removed == []
    assert "different question" in protected[0][1]


def test_question_scope_deletes_a_paraphrased_question():
    rows = rows_of(("Tell me about Gwen?", "x" * 100, "Behavioral & Career", "Easy"),
                   ("Please tell me about Gwen?", "x" * 100, "Behavioral & Career", "Easy"))
    qvectors = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)  # cosine 1
    removed, _ = dd.plan_removals(rows, [[0, 1]], [0, 1],
                                  [rows[0]["answer"], rows[1]["answer"]],
                                  scope="question", qvectors=qvectors)
    assert removed == [1]


def test_subject_scope_protects_a_row_from_another_subject():
    rows = rows_of(("Q1", "x" * 100, "Java", "Easy"), ("Q2", "x" * 100, "React", "Easy"))
    removed, protected = dd.plan_removals(rows, [[0, 1]], [0, 1],
                                          [rows[0]["answer"], rows[1]["answer"]],
                                          scope="subject")
    assert removed == []
    assert "different subject" in protected[0][1]


def test_the_last_row_of_a_cell_is_never_removed():
    rows = rows_of(("Java one", "x" * 100, "Java", "Easy"),
                   ("React one", "x" * 120, "React", "Easy"),
                   ("React two", "x" * 110, "React", "Easy"))
    removed, protected = dd.plan_removals(rows, [[0, 1, 2]], [0, 1, 2],
                                          [r["answer"] for r in rows], scope="answer")
    assert removed == [2], "React keeps one, Java's only row is protected"
    assert protected and "last row" in protected[0][1]


def test_apply_writes_the_surviving_rows_and_keeps_a_backup(tmp_path, monkeypatch):
    src = tmp_path / "combined_dataset.csv"
    with open(src, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=dd.COLS)
        writer.writeheader()
        for i in range(3):
            writer.writerow({"prompt": "", "question": f"q{i}", "answer": "a" * 400,
                             "subject": "Java", "difficulty": "Easy", "data_flag": ""})
    monkeypatch.setattr(dd.sa, "CSV_PATH", src)

    rows = list(csv.DictReader(open(src, encoding="utf-8")))
    dd.apply_removals(rows, [1])

    kept = list(csv.DictReader(open(src, encoding="utf-8")))
    assert [r["question"] for r in kept] == ["q0", "q2"]
    assert (tmp_path / "combined_dataset.backup.csv").exists()
