"""Regenerate the weakest answers in combined_dataset.csv with an LLM.

Targets answers that are boilerplate templates or exact duplicates of another
row's answer. Writes results to a separate file; never edits the dataset until
you explicitly run --apply.

    python regenerate_answers.py --dry-run
    python regenerate_answers.py --limit 20 --out pilot.csv
    python regenerate_answers.py --workers 8
    python regenerate_answers.py --apply answers_regenerated.csv

Needs an OpenAI-compatible endpoint:
    export OPENAI_API_KEY=...        # required
    export OPENAI_BASE_URL=...       # optional, default https://api.openai.com/v1
    export OPENAI_MODEL=gpt-4o-mini  # optional
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = Path(__file__).resolve().parent
SRC = BASE / "combined_dataset.csv"
COLS = ["prompt", "question", "answer", "subject", "difficulty", "data_flag"]
OUT_COLS = ["question", "subject", "difficulty", "old_answer", "new_answer", "status"]

TEMPLATES = [
    "can be understood as a core concept",
    "A good answer should address the requirements",
]
BANNED = [
    "can be understood as a core concept",
    "A good answer should address the requirements",
    "as an ai language model",
    "certainly!",
    "of course!",
]

SYSTEM = (
    "You are a senior engineer writing model interview answers for a study dataset. "
    "Answer in the first person, as the candidate, in 120-220 words of flowing prose. "
    "Be concrete about the technique, the mechanism, the trade-off or the debugging step, "
    "but do NOT invent benchmark numbers, company names, dates or metrics — describe what "
    "you would do and why, not fabricated results. "
    "Plain text only: no markdown, no bullet lists, no bold, no headings. "
    "No preamble, no restating the question, no filler openers like 'Certainly' or "
    "'Of course', and no generic sentence that could apply to any topic."
)

USER_TMPL = (
    "Subject: {subject}\nDifficulty: {difficulty}\nInterview question: {question}\n\n"
    "Write the answer a strong candidate would give."
)


STOP = threading.Event()  # set when the provider says we are out of quota


def norm(text: str) -> str:
    return " ".join(text.lower().split())


def pick_targets(rows: list[dict], only_template: bool = False) -> list[dict]:
    counts = Counter(norm(r["answer"]) for r in rows)
    picked = []
    for r in rows:
        templated = any(t in r["answer"] for t in TEMPLATES)
        duplicated = counts[norm(r["answer"])] > 1
        if templated or (duplicated and not only_template):
            picked.append(r)
    return picked


def validate(answer: str, seen: set[str]) -> str | None:
    """Return a rejection reason, or None if the answer is acceptable."""
    a = (answer or "").strip()
    if len(a) < 300:
        return "too short"
    if len(a) > 2500:
        return "too long"
    tail = a.rstrip().rstrip('\"\'”’)').rstrip()
    if tail[-1:] not in '.!?…':
        return "unfinished sentence"
    lowered = a.lower()
    for b in BANNED:
        if b in lowered:
            return f"contains banned phrase: {b!r}"
    if norm(a) in seen:
        return "duplicate of another generated answer"
    return None


def retry_hint(text: str) -> float | None:
    import re

    m = re.search(r"try again in ([0-9.]+)\s*s", text)
    return float(m.group(1)) if m else None


def call_llm(question: str, subject: str, difficulty: str,
             api_key: str, base_url: str, model: str,
             retries: int = 8, timeout: int = 60) -> str:
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER_TMPL.format(
                subject=subject, difficulty=difficulty, question=question)},
        ],
        "temperature": 0.7,
        "max_tokens": 900,  # enough for 220 words without truncating mid-sentence
    }).encode()

    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                # some providers sit behind Cloudflare and reject the default Python UA
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            body = e.read()[:400]
            text = body.decode("utf-8", "ignore")
            last = f"HTTP {e.code}: {body[:160]!r}"
            if e.code == 429 and "per day" in text:
                STOP.set()  # daily quota gone: never retry, never risk a paid overage
                raise RuntimeError("daily quota exhausted: " + last)
            if e.code in (429, 500, 502, 503, 504):
                # per-minute caps carry a reset hint; wait exactly that long
                wait = retry_hint(text) or (2 ** attempt)
                time.sleep(min(wait, 45) + random.random())
                continue
            break
        except Exception as e:  # noqa: BLE001 - network flakiness
            last = repr(e)
            time.sleep(2 ** attempt + random.random())
    raise RuntimeError(last or "unknown error")


def dry_run(rows: list[dict], targets: list[dict], only_template: bool = False) -> None:
    print(f"mode                 : {'boilerplate answers only' if only_template else 'boilerplate + duplicated answers'}")
    print(f"dataset rows          : {len(rows):,}")
    print(f"target rows           : {len(targets):,} ({100 * len(targets) / len(rows):.1f}%)")
    templated = sum(1 for r in targets if any(t in r["answer"] for t in TEMPLATES))
    print(f"  boilerplate answers : {templated:,}")
    if not only_template:
        print(f"  duplicated answers  : {len(targets) - templated:,} (most also boilerplate)")
    print("\nby subject (top 8):")
    for s, c in Counter(r["subject"] for r in targets).most_common(8):
        print(f"  {c:5d}  {s}")
    in_tok = sum(len(r["question"]) // 4 + 120 for r in targets)
    out_tok = len(targets) * 300
    cost = in_tok / 1e6 * 0.15 + out_tok / 1e6 * 0.60
    print(f"\nestimated tokens      : ~{in_tok:,} in / ~{out_tok:,} out")
    print(f"estimated cost        : ~${cost:.2f} with gpt-4o-mini (input $0.15/M, output $0.60/M)")
    print(f"free-tier days needed : ~{out_tok / 200_000:.1f} at Groq's 200k output tokens/day")
    print(f"wall time             : ~{len(targets) / 8 / 60:.0f} min at 8 workers, ~1 req/s each")

    print("\n--- sample prompt ---")
    r = targets[0]
    print(SYSTEM)
    print()
    print(USER_TMPL.format(subject=r["subject"], difficulty=r["difficulty"], question=r["question"])[:400])


def main() -> None:
    ap = argparse.ArgumentParser(description="Regenerate weak answers with an LLM")
    ap.add_argument("--dry-run", action="store_true", help="show targets, cost and a sample prompt")
    ap.add_argument("--limit", type=int, help="only process the first N targets (pilot)")
    ap.add_argument("--only-template", action="store_true",
                    help="regenerate only the boilerplate answers (smaller, cheaper run)")
    ap.add_argument("--workers", type=int, default=8, help="parallel requests (default 8)")
    ap.add_argument("--out", default="answers_regenerated.csv", help="output CSV")
    ap.add_argument("--max-requests", type=int, help="hard cap on API calls this run")
    ap.add_argument("--resume", action="store_true",
                    help="skip questions already present in --out (continue a stopped run)")
    ap.add_argument("--apply", metavar="FILE", help="merge a reviewed output CSV into the dataset")
    args = ap.parse_args()

    with open(SRC, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    # ---- apply mode -------------------------------------------------------
    if args.apply:
        path = BASE / args.apply
        if not path.exists():
            sys.exit(f"FATAL: {path} not found")
        with open(path, encoding="utf-8", newline="") as fh:
            fixes = [r for r in csv.DictReader(fh) if r["status"] == "ok"]
        by_q = {norm(r["question"]): r["new_answer"].strip() for r in fixes}
        applied = 0
        for r in rows:
            k = norm(r["question"])
            if k in by_q:
                r["answer"] = by_q[k]
                applied += 1
        backup = BASE / "combined_dataset_before_regeneration.csv"
        if not backup.exists():
            backup.write_bytes(SRC.read_bytes())
            print(f"backup written: {backup.name}")
        tmp = SRC.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, SRC)
        print(f"applied {applied:,} new answers -> {SRC.name}")
        return

    targets = pick_targets(rows, only_template=args.only_template)
    if not targets:
        sys.exit("nothing to regenerate — no templated or duplicated answers found")

    if args.dry_run:
        dry_run(rows, targets, args.only_template)
        return

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("FATAL: set OPENAI_API_KEY (never hardcode the key in this file)")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    out_path = BASE / args.out
    done_q: set[str] = set()
    if args.resume and out_path.exists():
        with open(out_path, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("status", "") == "ok":
                    done_q.add(norm(r["question"]))
        before = len(targets)
        targets = [t for t in targets if norm(t["question"]) not in done_q]
        print(f"resume: {before - len(targets):,} already done, {len(targets):,} left")

    if args.limit:
        targets = targets[: args.limit]
    if args.max_requests:
        targets = targets[: args.max_requests]
    if not targets:
        print("nothing left to do")
        return
    print(f"regenerating {len(targets):,} answers with {model} via {base_url} ({args.workers} workers)")

    seen: set[str] = set()
    done = ok = rejected = failed = 0
    mode = "a" if (args.resume and out_path.exists()) else "w"
    with open(out_path, mode, encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUT_COLS)
        if mode == "w":
            writer.writeheader()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(call_llm, r["question"], r["subject"], r["difficulty"],
                            api_key, base_url, model): r
                for r in targets
            }
            for fut in as_completed(futures):
                r = futures[fut]
                done += 1
                try:
                    new = fut.result()
                    reason = validate(new, seen)
                    if reason:
                        rejected += 1
                        writer.writerow({**{k: r[k] for k in ("question", "subject", "difficulty")},
                                         "old_answer": r["answer"], "new_answer": new,
                                         "status": f"rejected: {reason}"})
                    else:
                        seen.add(norm(new))
                        ok += 1
                        writer.writerow({**{k: r[k] for k in ("question", "subject", "difficulty")},
                                         "old_answer": r["answer"], "new_answer": new,
                                         "status": "ok"})
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    writer.writerow({**{k: r[k] for k in ("question", "subject", "difficulty")},
                                     "old_answer": r["answer"], "new_answer": "",
                                     "status": f"error: {e}"})
                if done % 100 == 0 or done == len(targets):
                    fh.flush()
                    print(f"  {done:,}/{len(targets):,}  ok={ok:,} rejected={rejected:,} failed={failed:,}")

    print(f"\nwrote {out_path.name}: ok={ok:,} rejected={rejected:,} failed={failed:,}")
    if STOP.is_set():
        print("STOPPED on quota exhaustion — nothing was charged. Continue later with:")
        print(f"  python regenerate_answers.py --only-template --resume --out {args.out}")
    else:
        print("Review a sample, then run: python regenerate_answers.py --apply", args.out)


if __name__ == "__main__":
    main()
