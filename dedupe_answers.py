"""Remove duplicate rows mechanically, keeping one representative per cluster.

Two lenses, because they find different defects:

    question  (default) rows whose questions match at 0.98 are the same question asked twice.
              Deleting one costs nothing: the question survives in the representative.
    answer    rows whose stripped answer bodies match at 0.98. This mostly finds different
              questions sharing one stock body, so deletion is scoped (see --scope) and the
              default only deletes when the question matches too.

Templated answers are never chosen as the representative of a question cluster but are still
eligible for deletion when their question duplicates another row: the question survives either
way. Coverage is a hard constraint - 138/138 subject x difficulty cells is part of the brief, so
the last row of a cell is never removed.

Dry run by default; --apply writes the CSV (with a one-off backup).

    python dedupe_answers.py                      # question basis, report only
    python dedupe_answers.py --basis answer       # the answer-body lens, all scopes shown
    python dedupe_answers.py --apply              # remove the question-basis duplicates
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import search_app as sa  # noqa: E402
import cluster_duplicates as cd  # noqa: E402

COLS = ["prompt", "question", "answer", "subject", "difficulty", "data_flag"]
MIN_CELL = 1  # never leave a subject x difficulty cell empty


def _cell_guard(rows: list[dict], cell: Counter, index: int,
                protected: list[tuple[int, str]]) -> bool:
    """True when the row may go: its cell keeps at least MIN_CELL rows without it."""
    key = (rows[index]["subject"], rows[index]["difficulty"])
    if cell[key] <= MIN_CELL:
        protected.append((index, f"last row in {key[0]} / {key[1]}"))
        return False
    cell[key] -= 1
    return True


def plan_question_removals(rows: list[dict], groups: list[list[int]],
                           specific: set[int], bodies: list[str],
                           qvectors: np.ndarray | None = None,
                           threshold: float = 0.98) -> tuple[list[int], list[tuple[int, str]]]:
    """One row per question cluster: prefer a specific answer, then the longest one.

    A row is deleted only when it is within `threshold` of the row that will be kept. Union-find
    groups chain (A~B~C with A and C unrelated), and on this dataset that turned 29 different
    TameMini-GS questions into one cluster whose worst pair scored 0.954 - deleting on that basis
    removes questions rather than duplicates.
    """
    def rank(index: int) -> tuple[int, int, int]:
        return (-int(index in specific), -len(bodies[index]), index)

    cell = Counter((r["subject"], r["difficulty"]) for r in rows)
    removed: list[int] = []
    protected: list[tuple[int, str]] = []
    for group in sorted(groups, key=len, reverse=True):
        ordered = sorted(group, key=rank)
        kept = ordered[0]
        for index in ordered[1:]:
            if qvectors is not None:
                similarity = float(qvectors[index] @ qvectors[kept])
                if similarity < threshold:
                    protected.append((index, f"too far from the kept row (cos {similarity:.2f})"))
                    continue
            if _cell_guard(rows, cell, index, protected):
                removed.append(index)
    return removed, protected


def plan_answer_removals(rows: list[dict], groups: list[list[int]], keep: list[int],
                         bodies: list[str], scope: str = "question",
                         qvectors: np.ndarray | None = None,
                         qthreshold: float = 0.98) -> tuple[list[int], list[tuple[int, str]]]:
    """One row per answer cluster, with the deletion scope deciding what may go.

    `groups` holds positions in the specific-body list; `keep[j]` maps back to the dataset row.
    """
    if scope not in ("question", "subject", "answer"):
        raise ValueError(f"unknown scope {scope!r}")
    cell = Counter((r["subject"], r["difficulty"]) for r in rows)
    removed: list[int] = []
    protected: list[tuple[int, str]] = []
    for group in sorted(groups, key=len, reverse=True):
        ordered = sorted(group, key=lambda j: (-len(bodies[j]), keep[j]))
        representative = keep[ordered[0]]
        for j in ordered[1:]:
            index = keep[j]
            if (scope == "subject"
                    and rows[index]["subject"] != rows[representative]["subject"]):
                protected.append((index, "different subject from its representative"))
                continue
            if scope == "question":
                if qvectors is None:
                    protected.append((index, "question vectors unavailable"))
                    continue
                similarity = float(qvectors[index] @ qvectors[representative])
                if similarity < qthreshold:
                    protected.append((index, f"different question (cos {similarity:.2f})"))
                    continue
            if _cell_guard(rows, cell, index, protected):
                removed.append(index)
    return removed, protected


def cell_counts(rows: list[dict], skip: set[int] | None = None) -> Counter:
    skip = skip or set()
    return Counter((r["subject"], r["difficulty"])
                   for i, r in enumerate(rows) if i not in skip)


def report(rows: list[dict], groups: list[list[int]], label: str,
           removed: list[int], protected: list[tuple[int, str]],
           examples: list[tuple[int, int]], show: int) -> None:
    dropped = set(removed)
    before_cells = cell_counts(rows)
    after_cells = cell_counts(rows, dropped)
    before_subjects = Counter(r["subject"] for r in rows)
    after_subjects = Counter(r["subject"] for i, r in enumerate(rows) if i not in dropped)

    print(f"rows                      : {len(rows):,}")
    print(f"{label:<26}: {len(groups):,} clusters "
          f"({sum(len(g) for g in groups):,} rows)")
    print(f"would remove              : {len(removed):,} rows")
    print(f"kept despite a duplicate  : {len(protected):,} rows")

    covered_before = sum(1 for c in before_cells.values() if c > 0)
    covered_after = sum(1 for c in after_cells.values() if c > 0)
    print(f"\ncells covered             : {covered_before}/{len(before_cells)} -> "
          f"{covered_after}/{len(after_cells)}")
    print(f"smallest cell             : {min(before_cells.values())} -> "
          f"{min(after_cells.values())} rows")
    print(f"cells losing rows         : "
          f"{sum(1 for k, v in after_cells.items() if v < before_cells[k]):,}")

    print("\nsubjects (before -> after):")
    for subject, count in before_subjects.most_common():
        delta = count - after_subjects[subject]
        if delta:
            print(f"  {subject:<28} {count:5,} -> {after_subjects[subject]:5,}  (-{delta})")
    print(f"  {'TOTAL':<28} {len(rows):5,} -> {len(rows) - len(dropped):5,}  "
          f"(-{len(dropped)})")

    if protected:
        print("\nkept despite a duplicate, first 5:")
        for index, reason in protected[:5]:
            print(f"  {reason}: {rows[index]['question'][:58]}")

    if show and examples:
        print(f"\nfirst {min(show, len(examples))} removals:")
        for index, keep_index in examples[:show]:
            print(f"  drop: {rows[index]['question'][:66]}")
            print(f"   for: {rows[keep_index]['question'][:66]}")

    print("\nnext: python audit_dataset.py combined_dataset.csv"
          "\n      python clean_answers.py"
          "\n      python build_final_workbook.py")


def apply_removals(rows: list[dict], removed: list[int]) -> Path:
    dropped = set(removed)
    kept = [r for i, r in enumerate(rows) if i not in dropped]
    src = sa.CSV_PATH
    backup = src.with_name("combined_dataset.backup.csv")
    if not backup.exists():
        backup.write_bytes(src.read_bytes())
        print(f"backup written: {backup.name}")
    tmp = src.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept)
    os.replace(tmp, src)
    print(f"removed {len(dropped):,} rows -> {src.name} now holds {len(kept):,}")
    return src


def main() -> int:
    ap = argparse.ArgumentParser(description="Remove duplicate rows, keep one per cluster")
    ap.add_argument("--apply", action="store_true", help="write the CSV (dry run otherwise)")
    ap.add_argument("--basis", choices=["question", "answer"], default="question",
                    help="what makes two rows duplicates (default question)")
    ap.add_argument("--scope", choices=["question", "subject", "answer"], default="question",
                    help="answer basis only: what may be deleted")
    ap.add_argument("--threshold", type=float, default=0.98,
                    help="cosine similarity that makes two items duplicates")
    ap.add_argument("--slot-min-count", type=int, default=25,
                    help="slot-filled skeleton recurrence that counts as a template")
    ap.add_argument("--opening-min-count", type=int, default=50,
                    help="verbatim sentence recurrence that counts as a template")
    ap.add_argument("--answer-prefix", type=int, default=200,
                    help="characters of the stripped body to embed")
    ap.add_argument("--show", type=int, default=5, help="example removals to print")
    args = ap.parse_args()

    rows, _ = sa.load_dataset()
    answers = [r["answer"] for r in rows]

    print("question vectors: building or loading the index")
    qvectors = sa.build_index(rows)

    bodies, info = cd.strip_template_sentences(answers, args.opening_min_count,
                                               args.slot_min_count,
                                               [r["subject"] for r in rows])
    keep = info["keep"]
    specific = set(keep)
    print(f"rows: {len(rows):,} | fully templated (never the representative): "
          f"{info['rows_fully_templated']:,}")

    if args.basis == "question":
        groups = cd.pair_clusters(qvectors, args.threshold)
        removed, protected = plan_question_removals(rows, groups, specific, bodies,
                                                    qvectors, args.threshold)
        examples = []
        for group in sorted(groups, key=len, reverse=True):
            ordered = sorted(group, key=lambda i: (-int(i in specific), -len(bodies[i]), i))
            examples.extend((i, ordered[0]) for i in ordered[1:4])
        report(rows, groups, "question clusters", removed, protected, examples, args.show)
    else:
        body_vectors, source = cd.body_vectors(
            [bodies[i] for i in keep], args.answer_prefix,
            args.opening_min_count, args.slot_min_count)
        print(f"body vectors: {source} for {len(keep):,} specific answers")
        groups = cd.pair_clusters(body_vectors, args.threshold)

        counts = {}
        for scope in ("question", "subject", "answer"):
            counts[scope] = len(plan_answer_removals(rows, groups, keep, bodies, scope,
                                                     qvectors, args.threshold)[0])
        print("\nwhat each answer-basis scope would remove:")
        for scope, count in counts.items():
            print(f"  {scope:<8} {count:5,} rows")
        print()

        removed, protected = plan_answer_removals(rows, groups, keep, bodies, args.scope,
                                                  qvectors, args.threshold)
        position = {index: j for j, index in enumerate(keep)}
        examples = [(keep[j], keep[sorted(g, key=lambda x: (-len(bodies[x]), keep[x]))[0]])
                    for g in sorted(groups, key=len, reverse=True)[:5]
                    for j in g[1:2]]
        report(rows, groups, f"answer clusters (scope {args.scope})",
               removed, protected, examples, args.show)

    if args.apply:
        apply_removals(rows, removed)
    else:
        print(f"\ndry run: nothing written (use --apply --basis {args.basis} to remove "
              f"these {len(removed):,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
