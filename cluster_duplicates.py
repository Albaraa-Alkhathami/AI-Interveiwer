#!/usr/bin/env python3
"""Measure paraphrase duplication: cluster near-identical questions and answers.

Exact-text dedupe only catches a minority of the defects in this dataset. This script
reuses the local BGE embeddings (no API calls, no tokens) to find rows that say the same
thing in different words, and reports how many rows a consolidation would actually remove.

A first run of the answer path reported 88% duplication, which was wrong: six stock
opening sentences cover about 85% of the answers, so prefix embeddings mostly measured the
template. Openings are therefore counted separately and stripped before answers are
compared - the bodies are what may be paraphrases.

    python cluster_duplicates.py                     # questions, openings, answers
    python cluster_duplicates.py --only openings     # no embedding needed
    python cluster_duplicates.py --only answers --thresholds 0.98,0.95 --json

Read-only: it never writes the dataset. Embedding caches land in embeddings/ (gitignored).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import search_app as sa  # noqa: E402

# The leading sentence of an answer: long enough to be a template, short enough to be one.
OPENING = re.compile(r"^\s*([^.!?]{20,240}[.!?])\s*")
MIN_BODY = 60  # a stripped answer below this has no comparable body left


def split_opening(text: str) -> tuple[str | None, str]:
    match = OPENING.match(text)
    if not match:
        return None, text
    return match.group(1).strip(), text[match.end():].strip()


def opening_stats(texts: list[str], min_count: int) -> dict:
    """How many answers open with a sentence that recurs, and which ones."""
    openings = [split_opening(t)[0] for t in texts]
    counts = Counter(o.casefold() for o in openings if o)
    stock = {o for o, n in counts.items() if n >= min_count}
    rows_with_stock = sum(1 for o in openings if o and o.casefold() in stock)
    return {
        "distinct_openings": len(counts),
        "stock_openings": len(stock),
        "rows_with_stock_opening": rows_with_stock,
        "largest": [n for _o, n in counts.most_common(5)],
        "top": [o[:90] for o, _n in counts.most_common(3)],
    }


SENTENCE = re.compile(r"(?<=[.!?])\s+")


def strip_template_sentences(texts: list[str], min_count: int) -> tuple[list[str], dict]:
    """Drop every sentence that recurs across the dataset, wherever it sits.

    Stripping only the leading sentence was not enough: the template continues into the
    body ("(3) add buffering and explicit backpressure..." is shared by 1,337 answers), so a
    front-to-back strip merely walks into the next stock sentence. Removing all recurring
    sentences leaves the part that is actually specific to the question.
    """
    per_text = [SENTENCE.split(" ".join(t.split())) for t in texts]
    counts = Counter(s.casefold() for sents in per_text for s in sents if len(s) > 20)
    stock = {s for s, n in counts.items() if n >= min_count}

    bodies, keep, removed = [], [], 0
    for i, (text, sents) in enumerate(zip(texts, per_text)):
        kept = [s for s in sents if not (len(s) > 20 and s.casefold() in stock)]
        body = " ".join(kept).strip()
        removed += max(0, len(text) - len(body))
        if len(body) >= MIN_BODY:
            bodies.append(body)
            keep.append(i)
        else:
            # nothing survives the strip: the whole answer is template, so it has no
            # specific text to compare and must not be counted as a paraphrase of another
            bodies.append(text)
    return bodies, {
        "stock_sentences": len(stock),
        "rows_with_specific_text": len(keep),
        "rows_fully_templated": len(texts) - len(keep),
        "chars_removed": removed,
        "keep": keep,
    }


def body_vectors(bodies: list[str], prefix: int, min_count: int) -> tuple[np.ndarray, str]:
    """Embed a body prefix, cached by content hash.

    A near-duplicate answer is recognisable from its opening once the shared template has
    been removed, and embedding 200 characters is several times cheaper than the whole text.
    """
    texts = [" ".join(b.split())[:prefix] for b in bodies]
    sha = hashlib.sha1("\n".join(texts).encode("utf-8")).hexdigest()
    slug = sa.MODEL_NAME.replace("/", "_")
    npy = sa.CACHE_DIR / f"bodies_p{prefix}_m{min_count}_{slug}.npy"
    meta = npy.with_suffix(".json")
    if npy.exists() and meta.exists():
        recorded = json.loads(meta.read_text(encoding="utf-8"))
        if recorded.get("sha") == sha and recorded.get("rows") == len(texts):
            return np.load(npy), "cache"

    sa.CACHE_DIR.mkdir(exist_ok=True)
    embedder = sa.Embedder()
    parts, done = [], 0
    for chunk in embedder.encode(texts, batch_size=256):
        parts.append(chunk)
        done += len(chunk)
        if done % 5000 == 0 or done == len(texts):
            print(f"  embedded {done:,}/{len(texts):,} bodies", flush=True)
    vectors = np.vstack(parts)
    np.save(npy, vectors)
    meta.write_text(json.dumps({"sha": sha, "rows": len(texts), "prefix": prefix,
                                "opening_min_count": min_count}), encoding="utf-8")
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
            "reducible_rows": rows_in - len(groups),
            "largest": [len(g) for g in biggest],
            "examples": [[texts[i][:90] for i in g[:3]] for g in biggest[:2]],
        }
        out.append(result)
        print(f"{name:<10} cos>{t:<5} clusters={len(groups):>6,} "
              f"rows_in_clusters={rows_in:>6,} reducible={rows_in - len(groups):>6,} "
              f"largest={result['largest']}", flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Cluster near-duplicate questions/answers")
    ap.add_argument("--only", choices=["questions", "answers", "openings"])
    ap.add_argument("--thresholds", default="0.98,0.95,0.92")
    ap.add_argument("--answer-prefix", type=int, default=200)
    ap.add_argument("--opening-min-count", type=int, default=50,
                    help="a leading sentence repeated at least this often is a template")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    thresholds = [float(x) for x in args.thresholds.split(",")]
    rows, _ = sa.load_dataset()
    answers = [r["answer"] for r in rows]
    print(f"rows: {len(rows):,}")

    results: list[dict] = []

    if args.only in (None, "questions", "openings"):
        stats = opening_stats(answers, args.opening_min_count)
        print(f"openings   distinct={stats['distinct_openings']:,} "
              f"stock={stats['stock_openings']:,} "
              f"rows_with_stock={stats['rows_with_stock_opening']:,} "
              f"largest={stats['largest']}")
        if args.only == "openings":
            results.append({"kind": "openings", **stats})

    if args.only in (None, "questions"):
        qv = sa.build_index(rows)
        qtexts = [f"{r['subject']}. {r['question']}" for r in rows]
        results += summarise("questions", qtexts, qv, thresholds)

    if args.only in (None, "answers"):
        bodies, strip_info = strip_template_sentences(answers, args.opening_min_count)
        print(f"answers    template sentences={strip_info['stock_sentences']:,} "
              f"rows_with_specific_text={strip_info['rows_with_specific_text']:,} "
              f"rows_fully_templated={strip_info['rows_fully_templated']:,}")
        specific = [bodies[i] for i in strip_info["keep"]]
        av, source = body_vectors(specific, args.answer_prefix, args.opening_min_count)
        print(f"answers: body vectors from {source} for {len(specific):,} specific rows "
              f"(prefix {args.answer_prefix} chars)")
        results += summarise("answers", specific, av, thresholds)

    if args.json:
        print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
