"""Tests for audit_dataset.py (the script shipped with the interview-dataset skill)."""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SCRIPT = BASE / "audit_dataset.py"
HEADER = ["prompt", "question", "answer", "subject", "difficulty"]
GOOD = (
    "I keep the failure isolated by wrapping the write in a retry with an idempotency key, "
    "so a duplicate delivery cannot apply the change twice. When the queue backs up I shed "
    "load at the edge instead of letting the consumer fall further behind."
)


def write_csv(path: Path, rows: list[list[str]], header: list[str] = HEADER) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, timeout=120)


def test_clean_dataset_passes(tmp_path):
    csv_path = tmp_path / "clean.csv"
    write_csv(csv_path, [
        ["p", "How do you bound a queue?", GOOD, "DevOps", "Hard"],
        ["p", "How do you isolate a failure?", GOOD + " Each hop retries once.", "DevOps", "Medium"],
    ])
    out = run(str(csv_path))
    assert out.returncode == 0, out.stdout + out.stderr
    assert "VERDICT: PASS" in out.stdout


def test_invented_metric_fails_the_audit(tmp_path):
    csv_path = tmp_path / "bad.csv"
    write_csv(csv_path, [["p", "How fast?", "We improved training speed by 3.1x.", "ML", "Hard"]])
    out = run(str(csv_path))
    assert out.returncode == 1
    assert "invented specifics" in out.stdout


def test_company_name_fails_the_audit(tmp_path):
    csv_path = tmp_path / "bad2.csv"
    write_csv(csv_path, [["p", "How?", "At Google we shard by tenant.", "ML", "Hard"]])
    assert run(str(csv_path)).returncode == 1


def test_duplicate_questions_fail_but_duplicate_answers_only_warn(tmp_path):
    csv_path = tmp_path / "dupes.csv"
    write_csv(csv_path, [
        ["p", "Same question?", GOOD, "ML", "Easy"],
        ["p", "same question?", GOOD + " more.", "ML", "Hard"],
        ["p", "Different question?", GOOD + " more.", "ML", "Hard"],
    ])
    out = run(str(csv_path))
    assert out.returncode == 1
    assert "duplicate question texts" in out.stdout
    assert "shared answer texts" in out.stdout


def test_custom_column_names_and_json_output(tmp_path):
    csv_path = tmp_path / "custom.csv"
    write_csv(csv_path,
              [["p", "Q here", "We cut memory use by 60% on the same box.", "ML", "Easy"]],
              header=["prompt", "q", "a", "topic", "level"])
    out = run(str(csv_path), "--question", "q", "--answer", "a",
              "--subject", "topic", "--difficulty", "level", "--json")
    assert out.returncode == 1
    assert '"metrics": 1' in out.stdout


def test_coverage_block_appears_only_with_subject_and_difficulty(tmp_path):
    csv_path = tmp_path / "cov.csv"
    write_csv(csv_path, [
        ["p", "Q1?", GOOD, "CUDA", "Easy"],
        ["p", "Q2?", GOOD + " x.", "CUDA", "Hard"],
    ])
    out = run(str(csv_path))
    # one subject x three standard levels = 3 cells, Medium missing -> 2/3
    assert "coverage    : 2/3 cells" in out.stdout
    assert "('CUDA', 'Medium')" in out.stdout


def test_missing_required_columns_exits_with_message(tmp_path):
    csv_path = tmp_path / "wrong.csv"
    write_csv(csv_path, [["p", "Q?", "A."]], header=["prompt", "query", "body"])
    out = run(str(csv_path))
    assert out.returncode != 0
    assert "need question and answer columns" in (out.stdout + out.stderr)


def test_company_named_in_the_question_is_not_a_blocker(tmp_path):
    """Context supplied by the question is not an invented company name."""
    csv_path = tmp_path / "qa.csv"
    write_csv(csv_path, [
        ["p", "How is Magento connected with Google Analytics?",
         "Magento integrates with Google Analytics to report on traffic.", "Magento", "Easy"],
    ])
    out = run(str(csv_path))
    assert out.returncode == 0, out.stdout
    assert "invented specifics  : 0" in out.stdout


def test_metric_quoted_from_the_question_is_not_a_blocker(tmp_path):
    csv_path = tmp_path / "qm.csv"
    write_csv(csv_path, [
        ["p", "You must cut response time by 30%. How?",
         "I would start by profiling to find what consumes the 30%, then cache.", "ML", "Hard"],
    ])
    out = run(str(csv_path))
    assert out.returncode == 0, out.stdout
