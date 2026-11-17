#!/usr/bin/env python3
"""Measure paraphrase duplication: cluster near-identical questions and answers.

Exact-text dedupe only catches a round dozen of the defects in this dataset. This
script reuses the local BGE embeddings (no API calls, no tokens) to find rows that say
the same thing in different words, at a range of cosine thresholds, and reports how many
rows a consolidation would actually remove.

    python cluster_duplicates.py                     # questions + answers
    python cluster_duplicates.py --only questions
    python cluster_duplicates.py --thresholds 0.98,0.95 --json

Read-only: it never writes the dataset. Embedding caches land in embeddings/ (gitignored).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import search_app as sa  # noqa: E402


def answer_vectors(rows: list[dict], prefix: int) -> tuple[np.ndarray, str]:
    """Embed an answer prefix, cached by content hash.

    A near-duplicate answer is recognisable from its opening: the boilerplate and the
    shared text both start the same way, and embedding 200 characters is several times
    cheaper than embedding the whole answer.
    """
    texts = [" ".join(r["answer"].split())[:prefix] for r in rows]
    sha = hashlib.sha1("\n".join(texts).encode("utf-8")).hexdigest()
    slug = sa.MODEL_NAME.replace("/", "_")
    npy = sa.CACHE_DIR / f"answers_p{prefix}_{slug}.npy"
    meta = npy.with_suffix(".json")
    if npy.exists() and meta.exists():
        if json.loads(meta.read_text(encoding="utf-8")).get("sha") == sha:
            return np.load(npy), "cache"

    sa.CACHE_DIR.mkdir(exist_ok=True)
    embedder = sa.Embedder()
    parts, done = [], 0
    for chunk in embedder.encode(texts, batch_size=256):
        parts.append(chunk)
        done += len(chunk)
        if done % 5000 == 0 or done == len(texts):
            print(f"  embedded {done:,}/{len(texts):,} answers", flush=True)
    vectors = np.vstack(parts)
    np.save(npy, vectors)
    meta.write_text(json.dumps({"sha": sha, "rows": len(texts), "prefix": prefix}),
                    encoding="utf-8")
    return vectors, "encoded"


def pair_clusters(vectors: np.ndarray, threshold: float, block: int = 1024) -> list[list[int]]:
    """Union rows whose cosine similarity exceeds the threshold, then return the groups."""
    n = len(vectors)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for start in range(0, n, block):
        stop = min(start + block, n)
        sims = vectors[start:stop] @ vectors.T          # unit vectors: dot product is cosine
        rows_idx, cols_idx = np.nonzero(sims > threshold)
        for r, c in zip(rows_idx.tolist(), cols_idx.tolist()):
            a, b = start + r, c
            if b <= a:                                   # each pair once, no self-matches
                continue
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
        if stop % 5000 == 0 or stop == n:
            print(f"  compared {stop:,}/{n:,} at {threshold}", flush=True)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [g for g in groups.values() if len(g) > 1]


def summarise(name: str, texts: list[str], vectors: np.ndarray, thresholds: list[float],
              examples: int = 3) -> list[dict]:
    out = []
    n = len(texts)
    for t in thresholds:
        groups = pair_clusters(vectors, t)
        rows_in = sum(len(g) for g in groups)
        biggest = sorted(groups, key=len, reverse=True)[:examples]
        result = {
            "kind": name,
            "threshold": t,
            "rows": n,
            "clusters": len(groups),
            "rows_in_clusters": rows_in,
            "reducible_rows": n - (n - rows_in) - 0,   # rows beyond one per cluster
            "largest": [len(g) for g in biggest],
            "examples": [[texts[i][:90] for i in g[:3]] for g in biggest[:2]],
        }
        result["reducible_rows"] = rows_in - len(groups)
        out.append(result)
        print(f"{name:<10} cos>{t:<5} clusters={len(groups):>6,} "
              f"rows_in_clusters={rows_in:>6,} reducible={rows_in - len(groups):>6,} "
              f"largest={result['largest']}", flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Cluster near-duplicate questions/answers")
    ap.add_argument("--only", choices=["questions", "answers"])
    ap.add_argument("--thresholds", default="0.98,0.95,0.92")
    ap.add_argument("--answer-prefix", type=int, default=200)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    thresholds = [float(x) for x in args.thresholds.split(",")]
    rows, _ = sa.load_dataset()
    print(f"rows: {len(rows):,}")

    results = []
    if args.only in (None, "questions"):
        qv = sa.build_index(rows)
        qtexts = [f"{r['subject']}. {r['question']}" for r in rows]
        results += summarise("questions", qtexts, qv, thresholds)

    if args.only in (None, "answers"):
        av, source = answer_vectors(rows, args.answer_prefix)
        print(f"answers: vectors from {source} (prefix {args.answer_prefix} chars)")
        atexts = [r["answer"] for r in rows]
        results += summarise("answers", atexts, av, thresholds)

    if args.json:
        print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
