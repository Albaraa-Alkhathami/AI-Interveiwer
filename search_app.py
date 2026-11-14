"""Semantic question search over combined_dataset.csv.

    python search_app.py --validate
    python search_app.py --query "why does my vue component re-render so much"
    python search_app.py --query "memory corruption" --subject C++ --difficulty Hard -k 5
    python search_app.py --interactive

Embeddings come from BAAI/bge-small-en-v1.5 running locally through onnxruntime;
the model files live in embeddings/model/ and the vector index is cached in
embeddings/*.npy, rebuilt automatically when the CSV changes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import textwrap
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent
CSV_PATH = BASE / "combined_dataset.csv"
CACHE_DIR = BASE / "embeddings"
MODEL_DIR = CACHE_DIR / "model"
MODEL_NAME = "BAAI/bge-small-en-v1.5"
MAX_TOKENS = 64
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
REQUIRED = ["prompt", "question", "answer", "subject", "difficulty"]
DIFFICULTIES = ("Easy", "Medium", "Hard")


# --------------------------------------------------------------------------- #
# 1. load and validate
# --------------------------------------------------------------------------- #
def load_dataset(path: Path = CSV_PATH) -> tuple[list[dict], dict]:
    if not path.exists():
        sys.exit(f"FATAL: dataset not found: {path}")

    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            sys.exit(f"FATAL: missing required column(s): {', '.join(missing)}")
        rows = list(reader)

    if not rows:
        sys.exit("FATAL: dataset is empty")

    empties = {
        "question": sum(1 for r in rows if not (r.get("question") or "").strip()),
        "answer": sum(1 for r in rows if not (r.get("answer") or "").strip()),
        "subject": sum(1 for r in rows if not (r.get("subject") or "").strip()),
        "difficulty": sum(1 for r in rows if not (r.get("difficulty") or "").strip()),
    }
    bad_diff = sorted({(r.get("difficulty") or "").strip() for r in rows} - set(DIFFICULTIES))
    seen, dupes = set(), 0
    for r in rows:
        key = " ".join((r.get("question") or "").lower().split())
        if key in seen:
            dupes += 1
        seen.add(key)

    report = {
        "rows": len(rows),
        "subjects": len({(r.get("subject") or "").strip() for r in rows}),
        "difficulty_counts": {
            d: sum(1 for r in rows if (r.get("difficulty") or "").strip() == d) for d in DIFFICULTIES
        },
        "empty_fields": empties,
        "duplicate_questions": dupes,
        "flagged_limited_data": sum(1 for r in rows if (r.get("data_flag") or "").strip()),
    }

    fatal = []
    if any(empties.values()):
        fatal.append(f"empty values in required fields {empties}")
    if bad_diff:
        fatal.append(f"unexpected difficulty values: {bad_diff}")
    if dupes:
        fatal.append(f"{dupes} duplicate question texts")
    if fatal:
        sys.exit("FATAL: validation failed -> " + "; ".join(fatal))

    for r in rows:
        for f in ("question", "answer", "subject", "difficulty"):
            r[f] = r[f].strip()
    return rows, report


def print_report(report: dict) -> None:
    print("DATASET VALIDATION: PASS")
    print(f"  file            : {CSV_PATH.name}")
    print(f"  rows            : {report['rows']:,}")
    print(f"  subjects        : {report['subjects']}")
    print("  difficulty      : " + ", ".join(f"{k} {v:,}" for k, v in report["difficulty_counts"].items()))
    print(f"  duplicate texts : {report['duplicate_questions']}")
    print(f"  empty fields    : {report['empty_fields']}")
    print(f"  limited-data rows: {report['flagged_limited_data']}")


# --------------------------------------------------------------------------- #
# 2. embeddings (local ONNX, batched)
# --------------------------------------------------------------------------- #
class Embedder:
    def __init__(self, model_dir: Path = MODEL_DIR, threads: int | None = None):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        if not (model_dir / "model.onnx").exists():
            sys.exit(f"FATAL: model not found in {model_dir}")
        self.tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tok.enable_truncation(max_length=MAX_TOKENS)
        self.tok.enable_padding()

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads or os.cpu_count() or 4
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(str(model_dir / "model.onnx"), opts,
                                         providers=["CPUExecutionProvider"])
        self.input_names = [i.name for i in self.sess.get_inputs()]

    def encode(self, texts, batch_size: int = 256):
        for start in range(0, len(texts), batch_size):
            batch = list(texts[start:start + batch_size])
            enc = self.tok.encode_batch(batch)
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.input_names:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.sess.run(None, feed)[0]
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(axis=1) / np.maximum(m.sum(axis=1), 1e-9)
            pooled /= np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
            yield pooled.astype(np.float32)

    def encode_one(self, text: str) -> np.ndarray:
        return next(self.encode([text], batch_size=1))[0]


def fingerprint(path: Path) -> str:
    """Hash only the fields that get embedded.

    Hashing the whole file means editing an answer throws away a valid index and
    spends ~10 minutes re-embedding identical texts; this keeps the cache alive
    across answer-only updates while still invalidating on any question, subject
    or ordering change.
    """
    h = hashlib.sha1()
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            text = f"{row['subject']}. {row['question']}"
            h.update(" ".join(text.split()).encode("utf-8") + b"\n")
    return h.hexdigest()


def cache_paths():
    slug = MODEL_NAME.replace("/", "_")
    return CACHE_DIR / f"questions_{slug}.npy", CACHE_DIR / f"meta_{slug}.json"


def build_index(rows: list[dict], rebuild: bool = False) -> np.ndarray:
    npy, meta_file = cache_paths()
    meta = {"model": MODEL_NAME, "rows": len(rows), "text_sha1": fingerprint(CSV_PATH)}

    if not rebuild and npy.exists() and meta_file.exists():
        if json.loads(meta_file.read_text(encoding="utf-8")) == meta:
            print(f"Loaded cached index: {npy.name} ({len(rows):,} vectors)")
            return np.load(npy)
        print("Index cache is stale for this dataset -> rebuilding")

    CACHE_DIR.mkdir(exist_ok=True)
    print(f"Embedding {len(rows):,} questions with {MODEL_NAME} ...")
    embedder = Embedder()
    texts = [f"{r['subject']}. {r['question']}" for r in rows]
    parts, done = [], 0
    for chunk in embedder.encode(texts, batch_size=256):
        parts.append(chunk)
        done += len(chunk)
        if done % 5000 == 0 or done == len(rows):
            print(f"  {done:,}/{len(rows):,}")
    vectors = np.vstack(parts)
    np.save(npy, vectors)
    meta_file.write_text(json.dumps(meta), encoding="utf-8")
    print(f"Saved {vectors.shape[0]:,} x {vectors.shape[1]} embeddings -> {npy.name}")
    return vectors


# --------------------------------------------------------------------------- #
# 3-5. semantic search, filters, top-k
# --------------------------------------------------------------------------- #
def search(rows, vectors, embedder: Embedder, query: str, k: int = 5,
           subject: str | None = None, difficulty: str | None = None):
    q = embedder.encode_one(QUERY_PREFIX + query)
    scores = vectors @ q

    allowed = np.ones(len(rows), dtype=bool)
    if subject:
        needle = subject.strip().casefold()
        allowed &= np.array([r["subject"].strip().casefold() == needle for r in rows])
    if difficulty:
        allowed &= np.array([r["difficulty"].lower() == difficulty.strip().lower() for r in rows])

    if not allowed.any():
        return []
    idx = np.flatnonzero(allowed)
    order = idx[np.argsort(-scores[idx])][:k]
    return [{**rows[i], "score": float(scores[i])} for i in order]


def show(query: str, results: list[dict], subject=None, difficulty=None) -> None:
    filt = [f"subject~'{subject}'"] if subject else []
    if difficulty:
        filt.append(f"difficulty={difficulty}")
    print(f"\nQuery: {query!r}" + (f"   [{' , '.join(filt)}]" if filt else ""))
    if not results:
        print("  no questions match those filters")
        return
    print("-" * 78)
    for rank, r in enumerate(results, 1):
        print(f"{rank}. [{r['score']:.3f}]  {r['subject']}  ·  {r['difficulty']}")
        print(textwrap.fill(r["question"], 100, initial_indent="   Q: ", subsequent_indent="      "))
        print(textwrap.fill(r["answer"], 100, initial_indent="   A: ", subsequent_indent="      "))
        print()


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Semantic search over the interview question dataset")
    ap.add_argument("--query", "-q", help="natural language query")
    ap.add_argument("--subject", "-s", help="filter by subject (exact match, case-insensitive)")
    ap.add_argument("--difficulty", "-d", help="filter by difficulty (Easy/Medium/Hard)")
    ap.add_argument("-k", type=int, default=5, help="number of results (default 5)")
    ap.add_argument("--validate", action="store_true", help="validate the dataset and exit")
    ap.add_argument("--rebuild", action="store_true", help="recompute the embedding index")
    ap.add_argument("--interactive", "-i", action="store_true", help="read queries from stdin")
    args = ap.parse_args()

    rows, report = load_dataset()
    print_report(report)
    if args.validate and not (args.query or args.interactive):
        return

    if args.difficulty and args.difficulty.lower() not in {d.lower() for d in DIFFICULTIES}:
        sys.exit(f"FATAL: difficulty must be one of {', '.join(DIFFICULTIES)}")
    if args.k < 1:
        sys.exit("FATAL: -k must be >= 1")

    if args.subject:
        names = sorted({r["subject"] for r in rows})
        needle = args.subject.strip().casefold()
        if needle not in {n.casefold() for n in names}:
            near = [n for n in names if needle in n.casefold()]
            hint = f"did you mean: {', '.join(near)}" if near else f"valid subjects: {', '.join(names)}"
            sys.exit(f"FATAL: unknown subject {args.subject!r} (exact match required); {hint}")

    vectors = build_index(rows, rebuild=args.rebuild)
    embedder = Embedder()

    if args.interactive:
        print("\nInteractive semantic search — type a question, blank line to quit.")
        while True:
            try:
                q = input("\nquery> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not q:
                break
            show(q, search(rows, vectors, embedder, q, args.k, args.subject, args.difficulty),
                 args.subject, args.difficulty)
        return

    if not args.query:
        ap.error("provide --query, --interactive or --validate")

    show(args.query, search(rows, vectors, embedder, args.query, args.k, args.subject, args.difficulty),
         args.subject, args.difficulty)


if __name__ == "__main__":
    main()
