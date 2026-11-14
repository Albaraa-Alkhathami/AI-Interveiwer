#!/usr/bin/env python3
"""Audit an interview question/answer dataset. Read-only, stdlib only.

    python audit.py combined_dataset.csv
    python audit.py data.csv --answer text --question q --json

Reports the defects that a question-level dedupe cannot see: answers shared
between different questions, fabricated metrics or company names, assistant
chatter, markdown leakage, typographic punctuation, and thin answers. Also
prints subject x difficulty coverage when those columns exist.

Exit status is 1 when a blocker is found (empty required fields, duplicate
question texts, or invented specifics), else 0. Warnings never fail the run.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from collections import Counter

METRIC = re.compile(r"\b\d+(?:\.\d+)?\s?%|\b\d+(?:\.\d+)?x\b|\b\d+\s?(?:ms|GB|MB|fps)\b")
COMPANIES = re.compile(
    r"\b(?:Google|Amazon|Microsoft|Netflix|Uber|Meta|Facebook|Apple|Nvidia|IBM|OpenAI|"
    r"DeepMind|Tesla|Airbnb|Spotify|LinkedIn|Twitter|Oracle|Salesforce)\b"
)
CHATTER = re.compile(
    r"(let me know|hope this helps|feel free to ask|i hope this|as an ai language model)",
    re.IGNORECASE,
)
FILLER = re.compile(r"^\s*(certainly|sure|of course)\s*[.,!]\s*", re.IGNORECASE)
MARKDOWN = re.compile(r"(\*\*[^*\n]+\*\*|```|^#{1,4} |^\s*[-*] )", re.MULTILINE)
TYPO = "\u2018\u2019\u201c\u201d\u2011\u2013\u2014\u2032\u00a0\u202f"
THIN_CHARS = 100


def norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def load(path: str) -> tuple[list[dict], list[str]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader), list(reader.fieldnames or [])


def pick(columns: list[str], wanted: str, name: str | None) -> str | None:
    if name and name in columns:
        return name
    if wanted in columns:
        return wanted
    return None


def audit(path: str, names: argparse.Namespace) -> dict:
    rows, columns = load(path)
    qcol = pick(columns, "question", names.question)
    acol = pick(columns, "answer", names.answer)
    scol = pick(columns, "subject", names.subject)
    dcol = pick(columns, "difficulty", names.difficulty)
    if not qcol or not acol:
        raise SystemExit(f"need question and answer columns; found {columns}")

    questions = [r.get(qcol) or "" for r in rows]
    answers = [r.get(acol) or "" for r in rows]
    words = [len(a.split()) for a in answers]

    empty = {c: sum(1 for r in rows if not (r.get(c) or "").strip())
             for c in [x for x in (qcol, acol, scol, dcol) if x]}

    q_counts = Counter(norm(q) for q in questions)
    dup_q = {k: v for k, v in q_counts.items() if v > 1}

    a_counts = Counter(norm(a) for a in answers)
    dup_a = {k: v for k, v in a_counts.items() if v > 1}

    metrics = [a for a in answers if METRIC.search(a)]
    companies = [a for a in answers if COMPANIES.search(a)]
    invented = [a for a in answers if METRIC.search(a) or COMPANIES.search(a)]
    chatter = [a for a in answers if CHATTER.search(a)]
    filler = [a for a in answers if FILLER.match(a)]
    markdown = [a for a in answers if MARKDOWN.search(a)]
    typo = [a for a in answers if any(c in a for c in TYPO)]
    thin = [a for a in answers if len(a.strip()) < THIN_CHARS]
    no_stop = [a for a in answers if a.strip() and a.rstrip()[-1:] not in ".!?\u2026\"')"]

    report = {
        "file": path,
        "rows": len(rows),
        "columns": columns,
        "empty_fields": empty,
        "duplicate_questions": {"groups": len(dup_q), "extra_rows": sum(v - 1 for v in dup_q.values())},
        "duplicate_answers": {"groups": len(dup_a), "extra_rows": sum(v - 1 for v in dup_a.values())},
        "invented_specifics": {"total": len(invented), "metrics": len(metrics), "company_names": len(companies)},
        "chatter": len(chatter),
        "filler_openers": len(filler),
        "markdown": len(markdown),
        "typographic_punctuation": len(typo),
        "thin_answers": thin,
        "no_terminal_punctuation": len(no_stop),
        "length_chars": {"min": min((len(a) for a in answers), default=0),
                         "median": int(statistics.median([len(a) for a in answers])) if answers else 0,
                         "max": max((len(a) for a in answers), default=0)},
        "length_words": {"min": min(words, default=0),
                         "median": int(statistics.median(words)) if words else 0,
                         "max": max(words, default=0)},
    }

    if scol and dcol:
        # Compare against the standard levels, not just the observed ones: deriving them
        # from the data makes a file with only Easy/Hard look 2/2 complete.
        observed = {(r.get(dcol) or "").strip() for r in rows} - {""}
        standard = ["Easy", "Medium", "Hard"]
        levels = standard + sorted(observed - set(standard))
        subjects = sorted({(r.get(scol) or "").strip() for r in rows} - {""})
        present = {((r.get(scol) or "").strip(), (r.get(dcol) or "").strip()) for r in rows}
        missing = [(s, d) for s in subjects for d in levels if (s, d) not in present]
        report["coverage"] = {
            "subjects": len(subjects), "levels": levels,
            "cells": len(subjects) * len(levels), "missing": len(missing),
            "missing_list": missing[:20],
            "subjects_under_20": sorted([(s, n) for s, n in Counter(
                (r.get(scol) or "").strip() for r in rows).items() if n < 20], key=lambda x: x[1]),
        }

    report["blockers"] = [
        name for name, hit in (
            ("empty required fields", any(empty.values())),
            ("duplicate question texts", bool(dup_q)),
            ("invented specifics", bool(invented)),
        ) if hit
    ]
    return report


def show(report: dict, examples: list[str]) -> None:
    print(f"file        : {report['file']}")
    print(f"rows        : {report['rows']:,}")
    print(f"answers     : median {report['length_words']['median']} words / "
          f"{report['length_chars']['median']} chars "
          f"(min {report['length_words']['min']}w, max {report['length_words']['max']}w)")
    print()
    print("blockers")
    for k, v in report["empty_fields"].items():
        print(f"  empty {k:<12}: {v:,}")
    dq, da = report["duplicate_questions"], report["duplicate_answers"]
    print(f"  duplicate questions : {dq['groups']:,} groups / {dq['extra_rows']:,} extra rows")
    inv = report["invented_specifics"]
    print(f"  invented specifics  : {inv['total']:,} "
          f"({inv['metrics']:,} metrics, {inv['company_names']:,} company names)")
    print()
    print("warnings")
    print(f"  shared answer texts : {da['groups']:,} groups / {da['extra_rows']:,} extra rows "
          f"<- different questions with the same answer")
    print(f"  assistant chatter   : {report['chatter']:,}")
    print(f"  filler openers      : {report['filler_openers']:,}")
    print(f"  markdown            : {report['markdown']:,}")
    print(f"  typographic punct.  : {report['typographic_punctuation']:,}")
    print(f"  under {THIN_CHARS} chars      : {len(report['thin_answers']):,}")
    print(f"  no terminal punct.  : {report['no_terminal_punctuation']:,}")
    cov = report.get("coverage")
    if cov:
        print()
        print(f"coverage    : {cov['cells'] - cov['missing']}/{cov['cells']} cells, "
              f"{cov['subjects']} subjects x {'/'.join(cov['levels'])}")
        if cov["missing_list"]:
            print(f"  missing   : {cov['missing_list'][:6]}{' ...' if cov['missing'] > 6 else ''}")
        if cov["subjects_under_20"]:
            print(f"  thin      : {cov['subjects_under_20']}")
    print()
    if report["blockers"]:
        print("VERDICT: FAIL — " + ", ".join(report["blockers"]))
    else:
        print("VERDICT: PASS (no blockers; warnings above are quality debt)")
    if examples:
        print()
        print("examples")
        for ex in examples:
            print(f"  {ex}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit an interview Q&A dataset (read-only)")
    ap.add_argument("csv")
    ap.add_argument("--question", help="question column name if not 'question'")
    ap.add_argument("--answer", help="answer column name if not 'answer'")
    ap.add_argument("--subject", help="subject column name")
    ap.add_argument("--difficulty", help="difficulty column name")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    report = audit(args.csv, args)
    if args.json:
        print(json.dumps(report, indent=2))
        return 1 if report["blockers"] else 0

    rows, _ = load(args.csv)
    acol = pick(list(rows[0].keys()) if rows else [], "answer", args.answer)
    examples = []
    seen = 0
    for r in rows:
        a = r.get(acol) if acol else ""
        m = METRIC.search(a or "") or COMPANIES.search(a or "")
        if m and seen < 3:
            start = max(0, m.start() - 45)
            examples.append(f"[invented] ...{(a or '')[start:m.end() + 15]!r}")
            seen += 1
    show(report, examples)
    return 1 if report["blockers"] else 0


if __name__ == "__main__":
    sys.exit(main())
