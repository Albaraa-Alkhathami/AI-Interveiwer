"""Remove near-duplicate answers mechanically, keeping one representative per cluster.

Templated answers are never touched. The pass only considers answers that still have specific
text after both template strips (verbatim 50x, slot-filled 25x), groups those by 0.98 cosine
over their stripped body, and keeps the row with the longest specific body in each group.

An answer-level cluster is not a duplicate row, though: the same stock body sits on questions
about cookies, Angular directives and drone telemetry. Deleting every row but one would remove
a question from the dataset to fix an answer, which is the wrong trade. So `--scope` decides
what may be deleted:

    question  (default) only rows whose question also matches the representative's at 0.98
    subject            only rows in the same subject as the representative
    answer             every non-representative row in the cluster (the raw measurement)

Coverage is a hard constraint: 138/138 subject x difficulty cells is part of the brief, so a
row is kept when removing it would empty its cell.

Dry run by default; --apply writes the CSV (with a one-off backup).

    python dedupe_answers.py                       # report, no writes
    python dedupe_answers.py --scope subject       # what a subject-only pass would remove
    python dedupe_answers.py --apply               # remove the default-scope rows
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


def plan_removals(rows: list[dict], groups: list[list[int]], keep: list[int],
                  bodies: list[str], scope: str = "question",
                  qvectors: np.ndarray | None = None,
                  qthreshold: float = 0.98) -> tuple[list[int], list[tuple[int, str]]]:
    """Decide which dataset rows to drop, one representative per cluster.

    `groups` holds positions in `bodies`; `keep[j]` maps such a position back to its dataset
    row. Returns (removed row indices, [(protected row index, reason)]). Cells are decremented
    as rows are removed, so two removals from the same cell cannot both pass a check that one
    row is left.
    """
    cell = Counter((r["subject"], r["difficulty"]) for r in rows)
    removed: list[int] = []
    protected: list[tuple[int, str]] = []
    if scope not in ("question", "subject", "answer"):
        raise ValueError(f"unknown scope {scope!r}")

    for group in sorted(groups, key=len, reverse=True):
        # richest answer first: the longest specific body, then dataset order
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
            key = (rows[index]["subject"], rows[index]["difficulty"])
            if cell[key] <= MIN_CELL:
                protected.append((index, f"last row in {key[0]} / {key[1]}"))
                continue
            cell[key] -= 1
            removed.append(index)

    return removed, protected


def cell_counts(rows: list[dict], skip: set[int] | None = None) -> Counter:
    skip = skip or set()
    return Counter((r["subject"], r["difficulty"])
                   for i, r in enumerate(rows) if i not in skip)


def report(rows: list[dict], bodies: list[str], keep: list[int], groups: list[list[int]],
           counts: dict[str, int], scope: str, protected: list[tuple[int, str]],
           removed: list[int], show: int) -> None:
    dropped = set(removed)
    before_cells = cell_counts(rows)
    after_cells = cell_counts(rows, dropped)
    before_subjects = Counter(r["subject"] for r in rows)
    after_subjects = Counter(r["subject"] for i, r in enumerate(rows) if i not in dropped)

    print(f"rows                      : {len(rows):,}")
    print(f"answers with specific text: {len(keep):,}")
    print(f"clusters @0.98            : {len(groups):,} "
          f"({sum(len(g) for g in groups):,} rows)")
    print("\nwhat each scope would remove:")
    for name in ("question", "subject", "answer"):
        print(f"  {name:<8} {counts[name]:5,} rows")

    print(f"\nthis run (scope {scope}): {len(removed):,} rows")
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

    if show and removed:
        print(f"\nfirst {min(show, len(removed))} removals:")
        kept_of = {j: sorted(g, key=lambda x: (-len(bodies[x]), keep[x]))[0]
                   for g in groups for j in g}
        position = {index: j for j, index in enumerate(keep)}
        for index in removed[:show]:
            twin = kept_of[position[index]]
            print(f"  drop: {rows[index]['question'][:66]}")
            print(f"   for: {rows[keep[twin]]['question'][:66]}")

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
    ap = argparse.ArgumentParser(description="Remove near-duplicate answers, keep one each")
    ap.add_argument("--apply", action="store_true", help="write the CSV (dry run otherwise)")
    ap.add_argument("--scope", choices=["question", "subject", "answer"], default="question",
                    help="what may be deleted; question only removes true question duplicates")
    ap.add_argument("--threshold", type=float, default=0.98,
                    help="cosine similarity that makes two bodies duplicates (default 0.98)")
    ap.add_argument("--question-threshold", type=float, default=0.98,
                    help="question cosine required to delete in question scope")
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
    subjects = [r["subject"] for r in rows]

    bodies, info = cd.strip_template_sentences(answers, args.opening_min_count,
                                               args.slot_min_count, subjects)
    keep = info["keep"]
    specific = [bodies[i] for i in keep]
    print(f"rows: {len(rows):,} | fully templated (left alone): "
          f"{info['rows_fully_templated']:,}")

    vectors, source = cd.body_vectors(specific, args.answer_prefix,
                                      args.opening_min_count, args.slot_min_count)
    print(f"body vectors: {source} for {len(specific):,} specific answers")
    groups = cd.pair_clusters(vectors, args.threshold)

    # the report shows all three scopes, so the question index is always needed
    print("question vectors: building or loading the index")
    qvectors = sa.build_index(rows)

    plans = {scope: plan_removals(rows, groups, keep, bodies, scope,
                                  qvectors, args.question_threshold)
             for scope in ("question", "subject", "answer")}
    removed, protected = plans[args.scope]

    report(rows, bodies, keep, groups, {s: len(plans[s][0]) for s in plans},
           args.scope, protected, removed, args.show)

    if args.apply:
        apply_removals(rows, removed)
    else:
        print(f"\ndry run: nothing written (use --apply --scope {args.scope} to remove "
              f"these {len(removed):,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
