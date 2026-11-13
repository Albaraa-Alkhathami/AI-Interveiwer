"""Generate the question/answer pairs the dataset is missing.

Plan-driven: fills the five empty subject x difficulty cells, tops up subjects
with fewer than 30 questions, and gives subjects that have no Hard questions a
few. Every candidate is embedded and compared against the existing 23,850-row
index, so near-duplicates are rejected before they reach the CSV.

    python generate_questions.py --plan
    python generate_questions.py --auto --budget 60
    python generate_questions.py --subject Education --difficulty Hard --count 5
    python generate_questions.py --apply questions_generated.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

import numpy as np

import search_app as sa

BASE = Path(__file__).resolve().parent
SRC = BASE / "combined_dataset.csv"
OUT = BASE / "questions_generated.csv"
COLS = ["prompt", "question", "answer", "subject", "difficulty", "data_flag"]
OUT_COLS = ["subject", "difficulty", "question", "answer", "nearest_score", "status"]

DIFFS = ("Easy", "Medium", "Hard")
MIN_PER_SUBJECT = 30
CELL_TARGET = 5
DUP_THRESHOLD = 0.90
STOP = threading.Event()

SYSTEM = (
    "You write realistic technical-interview questions and model answers for a study dataset. "
    "Return ONLY a JSON array, no prose around it. Each element: {\"question\": \"...\", "
    "\"answer\": \"...\"}. Answers are 120-220 words, first person, plain text, no markdown, "
    "no lists, no fabricated benchmark numbers or company names."
)

USER = (
    "Subject: {subject}\nDifficulty: {difficulty}\n"
    "Write {n} distinct interview questions with answers.\n"
    "Questions already in the dataset (do NOT repeat or paraphrase these):\n{examples}"
)


def norm(t: str) -> str:
    return " ".join(t.lower().split())


def build_plan(rows: list[dict]) -> list[tuple[str, str, int]]:
    counts = Counter(r["subject"] for r in rows)
    cells = Counter((r["subject"], r["difficulty"]) for r in rows)
    plan: list[tuple[str, str, int]] = []
    for subject in sorted(counts):
        for d in DIFFS:
            if cells[(subject, d)] == 0:
                plan.append((subject, d, CELL_TARGET))
        if counts[subject] < MIN_PER_SUBJECT:
            deficit = min(MIN_PER_SUBJECT - counts[subject], 10)
            if deficit > 0:
                weakest = min(DIFFS, key=lambda d: cells[(subject, d)])
                plan.append((subject, weakest, deficit))
    return plan


def examples_for(rows: list[dict], subject: str, k: int = 3) -> str:
    qs = [r["question"] for r in rows if r["subject"] == subject][:k]
    return "\n".join(f"- {q}" for q in qs) or "- (none yet)"


def retry_hint(text: str) -> float | None:
    m = re.search(r"try again in ([0-9.]+)\s*s", text)
    return float(m.group(1)) if m else None


def call_llm(payload_prompt: str, api_key: str, base_url: str, model: str,
             retries: int = 8, timeout: int = 90) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": payload_prompt}],
        "temperature": 0.9,
        "max_tokens": 3200,  # 5 pairs of ~200-word answers
    }).encode()
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions", data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
            })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            raw = e.read()[:400]
            text = raw.decode("utf-8", "ignore")
            last = f"HTTP {e.code}: {raw[:160]!r}"
            if e.code == 429 and "per day" in text:
                STOP.set()
                raise RuntimeError("daily quota exhausted: " + last)
            if e.code in (429, 500, 502, 503, 504):
                wait = retry_hint(text) or (2 ** attempt)
                time.sleep(min(wait, 45) + 0.5)
                continue
            break
        except Exception as e:  # noqa: BLE001
            last = repr(e)
            time.sleep(2 ** attempt + 0.5)
    raise RuntimeError(last or "unknown error")


def parse_pairs(raw: str) -> list[dict]:
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return []
    out = []
    for item in data:
        if isinstance(item, dict) and item.get("question") and item.get("answer"):
            out.append({"question": str(item["question"]).strip(),
                        "answer": str(item["answer"]).strip()})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate missing subject/difficulty questions")
    ap.add_argument("--plan", action="store_true", help="print the worklist and exit")
    ap.add_argument("--auto", action="store_true", help="run the whole plan")
    ap.add_argument("--subject"), ap.add_argument("--difficulty")
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--budget", type=int, default=40, help="max API calls this run")
    ap.add_argument("--apply", metavar="FILE", help="merge a reviewed output CSV into the dataset")
    args = ap.parse_args()

    rows, _ = sa.load_dataset()

    if args.apply:
        path = BASE / args.apply
        with open(path, encoding="utf-8", newline="") as fh:
            good = [r for r in csv.DictReader(fh) if r["status"] == "ok"]
        for r in good:
            rows.append({"prompt": f"Interview questions about {r['subject']}",
                         "question": r["question"], "answer": r["answer"],
                         "subject": r["subject"], "difficulty": r["difficulty"],
                         "data_flag": ""})
        counts = Counter(x["subject"] for x in rows)
        for x in rows:
            x["data_flag"] = "limited data (<20 questions)" if counts[x["subject"]] < 20 else ""
        backup = BASE / "combined_dataset_before_generation.csv"
        if not backup.exists():
            backup.write_bytes(SRC.read_bytes())
            print("backup written:", backup.name)
        tmp = SRC.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, SRC)
        print(f"appended {len(good)} generated questions -> {SRC.name} ({len(rows):,} rows)")
        return

    plan = build_plan(rows)
    if args.plan or not (args.auto or args.subject):
        print(f"worklist: {len(plan)} cells, {sum(n for _, _, n in plan)} questions")
        for s, d, n in plan:
            print(f"  {n:3d}  {s} / {d}")
        return

    if args.subject:
        if args.subject not in {r["subject"] for r in rows}:
            sys.exit(f"FATAL: unknown subject {args.subject!r}")
        plan = [(args.subject, args.difficulty or "Medium", args.count)]

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("FATAL: set OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    print(f"generating for {len(plan)} cells with {model} (budget {args.budget} calls)")
    npy, _ = sa.cache_paths()
    vectors = np.load(npy)
    embedder = sa.Embedder()
    seen = {norm(r["question"]) for r in rows}
    calls = 0
    ok = rejected = 0

    mode = "a" if OUT.exists() else "w"
    with open(OUT, mode, encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUT_COLS)
        if mode == "w":
            writer.writeheader()
        for subject, difficulty, need in plan:
            if STOP.is_set() or calls >= args.budget:
                break
            batch = min(need, 5)
            while batch > 0 and not STOP.is_set() and calls < args.budget:
                prompt = USER.format(subject=subject, difficulty=difficulty, n=batch,
                                     examples=examples_for(rows, subject))
                calls += 1
                try:
                    pairs = parse_pairs(call_llm(prompt, api_key, base_url, model))
                except Exception as e:  # noqa: BLE001
                    print(f"  ! {subject}/{difficulty}: {e}")
                    break
                for p in pairs:
                    q, a = p["question"], p["answer"]
                    key = norm(q)
                    if key in seen or len(a) < 300 or len(a) > 2500:
                        rejected += 1
                        continue
                    vec = embedder.encode_one(q)
                    nearest = float((vectors @ vec).max())
                    if nearest > DUP_THRESHOLD:
                        rejected += 1
                        writer.writerow({"subject": subject, "difficulty": difficulty, "question": q,
                                         "answer": a, "nearest_score": round(nearest, 3),
                                         "status": f"rejected: near-duplicate {nearest:.3f}"})
                        continue
                    seen.add(key)
                    ok += 1
                    writer.writerow({"subject": subject, "difficulty": difficulty, "question": q,
                                     "answer": a, "nearest_score": round(nearest, 3), "status": "ok"})
                fh.flush()
                batch -= len(pairs) if pairs else batch
                print(f"  {subject}/{difficulty}: +{len(pairs)} candidate(s), ok={ok} rejected={rejected}")

    print(f"\nwrote {OUT.name}: ok={ok} rejected={rejected}, api calls={calls}")
    if STOP.is_set():
        print("STOPPED on quota exhaustion — nothing was charged.")
    print("Review, then: python generate_questions.py --apply", OUT.name)


if __name__ == "__main__":
    main()
