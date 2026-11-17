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


# --------------------------------------------------------------------------- #
# Question basis: same question asked twice
# --------------------------------------------------------------------------- #
def test_question_cluster_keeps_the_specific_answer():
    rows = rows_of(("Tell me about Gwen?", "x" * 100, "Behavioral & Career", "Easy"),
                   ("Please tell me about Gwen?", "y" * 120, "Behavioral & Career", "Easy"))
    qvectors = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)
    bodies = ["x" * 100, "y" * 120]  # row 1 has specific text, row 0 is templated
    removed, protected = dd.plan_question_removals(rows, [[0, 1]], {1}, bodies, qvectors)
    assert removed == [0], "the row with specific text must be the representative"
    assert protected == []


def test_question_cluster_keeps_the_longest_answer_when_both_are_templated():
    rows = rows_of(("Q?", "x" * 80, "Java", "Easy"), ("Q?", "y" * 90, "Java", "Easy"))
    qvectors = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)
    removed, _ = dd.plan_question_removals(rows, [[0, 1]], set(), ["x" * 80, "y" * 90],
                                           qvectors)
    assert removed == [0], "no specific answer anywhere: the longer one survives"


def test_question_basis_never_deletes_a_row_far_from_the_kept_one():
    """Union-find chaining must not delete: A~B~C does not make A a duplicate of C."""
    rows = rows_of(("What was your role in TameMini-GS?", "x" * 80,
                    "3D Gaussian Splatting", "Medium"),
                   ("Why is TameMini-GS important?", "y" * 80,
                    "3D Gaussian Splatting", "Medium"))
    qvectors = np.array([[1.0, 0.0], [0.954, 0.30]], dtype=np.float32)
    removed, protected = dd.plan_question_removals(rows, [[0, 1]], set(), ["x", "y"], qvectors)
    assert removed == []
    assert protected and "too far" in protected[0][1]


def test_question_basis_never_empties_a_cell():
    rows = rows_of(("Q1?", "x" * 80, "Java", "Easy"),
                   ("Q1?", "y" * 80, "Java", "Hard"))
    qvectors = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)
    removed, protected = dd.plan_question_removals(rows, [[0, 1]], set(), ["x", "y"], qvectors)
    assert removed == []
    assert protected and "last row" in protected[0][1]


# --------------------------------------------------------------------------- #
# Answer basis: same body, which may sit on different questions
# --------------------------------------------------------------------------- #
def test_answer_basis_keeps_the_longest_specific_body():
    rows = rows_of(("short?", "x" * 40, "Java", "Easy"),
                   ("longer?", "x" * 120, "Java", "Easy"))
    removed, protected = dd.plan_answer_removals(
        rows, [[0, 1]], [0, 1], [rows[0]["answer"], rows[1]["answer"]], scope="answer")
    assert removed == [0]
    assert protected == []


def test_answer_basis_ties_go_to_dataset_order():
    rows = rows_of(("first?", "x" * 100, "Java", "Easy"),
                   ("second?", "x" * 100, "Java", "Easy"))
    removed, _ = dd.plan_answer_removals(
        rows, [[0, 1]], [0, 1], [rows[0]["answer"], rows[1]["answer"]], scope="answer")
    assert removed == [1]


def test_answer_scope_will_not_delete_a_different_question():
    rows = rows_of(("Why are cookies split by party?", "x" * 100, "Angular", "Easy"),
                   ("Why is entryComponents unnecessary?", "x" * 100, "Angular", "Easy"))
    qvectors = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    removed, protected = dd.plan_answer_removals(
        rows, [[0, 1]], [0, 1], [r["answer"] for r in rows],
        scope="question", qvectors=qvectors)
    assert removed == []
    assert "different question" in protected[0][1]


def test_answer_scope_deletes_a_paraphrased_question():
    rows = rows_of(("Tell me about Gwen?", "x" * 100, "Behavioral & Career", "Easy"),
                   ("Please tell me about Gwen?", "x" * 100, "Behavioral & Career", "Easy"))
    qvectors = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)
    removed, _ = dd.plan_answer_removals(
        rows, [[0, 1]], [0, 1], [r["answer"] for r in rows],
        scope="question", qvectors=qvectors)
    assert removed == [1]


def test_answer_basis_subject_scope_protects_another_subject():
    rows = rows_of(("Q1", "x" * 100, "Java", "Easy"), ("Q2", "x" * 100, "React", "Easy"))
    removed, protected = dd.plan_answer_removals(
        rows, [[0, 1]], [0, 1], [r["answer"] for r in rows], scope="subject")
    assert removed == []
    assert "different subject" in protected[0][1]


def test_answer_basis_last_row_of_a_cell_is_never_removed():
    rows = rows_of(("Java one", "x" * 100, "Java", "Easy"),
                   ("React one", "x" * 120, "React", "Easy"),
                   ("React two", "x" * 110, "React", "Easy"))
    removed, protected = dd.plan_answer_removals(
        rows, [[0, 1, 2]], [0, 1, 2], [r["answer"] for r in rows], scope="answer")
    assert removed == [2]
    assert protected and "last row" in protected[0][1]


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
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
