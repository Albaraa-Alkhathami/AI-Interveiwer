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
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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

# Rows whose text invents verifiable specifics. These read as proven results and are
# the highest-risk content in the dataset, so they are rewritten before anything else.
METRIC_RE = re.compile(r"\b\d+(?:\.\d+)?\s?%|\b\d+(?:\.\d+)?x\b|\b\d+\s?(?:ms|GB|MB|fps)\b")
COMPANY_RE = re.compile(
    r"\b(?:Google|Amazon|Microsoft|Netflix|Uber|Meta|Facebook|Apple|Nvidia|IBM|OpenAI|"
    r"DeepMind|Tesla|Airbnb|Spotify|LinkedIn|Twitter)\b"
)


def is_suspicious(answer: str, question: str = "") -> bool:
    """True when the answer states a metric or company the question never introduced.

    A name or figure the question itself supplies is not an invention: "How is Magento
    connected with Google Analytics?" must be allowed to say Google Analytics, and
    "reduce response time by at least 30%" must be allowed to repeat 30%. Rejecting those
    loops forever, because the prompt then asks for an answer containing what the guard bans.
    """
    asked = norm(question)
    for match in list(METRIC_RE.finditer(answer)) + list(COMPANY_RE.finditer(answer)):
        if norm(match.group(0)) not in asked:
            return True
    return False


def norm(text: str) -> str:
    return " ".join(text.lower().split())


def pick_targets(rows: list[dict], only_template: bool = False,
                 only_suspicious: bool = False,
                 only_duplicated: bool = False) -> list[dict]:
    if only_suspicious:
        return [r for r in rows if is_suspicious(r["answer"], r["question"])]
    counts = Counter(norm(r["answer"]) for r in rows)
    picked = []
    for r in rows:
        templated = any(t in r["answer"] for t in TEMPLATES)
        duplicated = counts[norm(r["answer"])] > 1
        if only_duplicated:
            # shared answer text is the worst defect: the answer fits a different question
            if duplicated:
                picked.append(r)
        elif templated or (duplicated and not only_template):
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
        # BANNED entries are mixed case; comparing them raw against lowercased text
        # silently skipped every phrase with a capital letter
        if b.lower() in lowered:
            return f"contains banned phrase: {b!r}"
    if norm(a) in seen:
        return "duplicate of another generated answer"
    return None


SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def templated_flags(rows: list[dict], min_count: int = 50) -> dict[int, bool]:
    """Map id(row) -> True when the answer contains a sentence that recurs dataset-wide.

    Roughly 61% of answers are assembled from a few hundred stock sentences; the whole-answer
    TEMPLATES list only sees 799 of them, so a repair pass needs this, not that list.
    """
    texts = [" ".join(r["answer"].split()) for r in rows]
    sents = [SENTENCE_RE.split(t) for t in texts]
    counts = Counter(s.casefold() for ss in sents for s in ss if len(s) > 20)
    stock = {s for s, n in counts.items() if n >= min_count}
    return {
        id(r): any(s.casefold() in stock for s in ss if len(s) > 20)
        for r, ss in zip(rows, sents)
    }


def quarantine_path() -> Path:
    return BASE / "quarantine.json"


def load_quarantine() -> dict[str, dict]:
    """Rows the guard has rejected before, with the reason and how often.

    Some questions cannot satisfy the guard by construction - "how much did you improve
    it?" invites a figure the answer may not state - so re-targeting them every day spends
    budget to reproduce the same rejection. The file is gitignored state, not data.
    """
    path = quarantine_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_quarantine(state: dict[str, dict]) -> None:
    path = quarantine_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def retry_hint(text: str) -> float | None:
    import re

    m = re.search(r"try again in ([0-9.]+)\s*s", text)
    return float(m.group(1)) if m else None


# Used for --only-suspicious rows: those questions ask "how much did you improve", which
# tempts the model into a plausible number of its own ("about 30% of the entries").
NO_METRICS = (
    "This question asks for a quantity, but state NO figures at all — no percentage, ratio,\n"
    "multiplier, count or measurement, not even an approximate one (never 'about 30%').\n"
    "Answer with the mechanism, the trade-off and how you would verify the improvement instead."
)


# Only failures a better prompt can actually fix. "duplicate of another generated answer"
# is excluded on purpose: the model cannot see the other answer, and asking it to "be more
# specific" reproduces the same text, so that retry buys nothing.
RETRYABLE = (
    "still contains a metric or company name",
    "contains banned phrase",
    "too short",
    "too long",
    "unfinished sentence",
)


def is_retryable(reason: str) -> bool:
    return reason.startswith(RETRYABLE)


def correction_for(reason: str, draft: str, question: str) -> str:
    """Turn a rejection reason into an instruction for one corrective retry.

    Retrying blindly with the same prompt mostly reproduces the same failure; naming the rule
    that failed is what converts a wasted request into a usable answer.
    """
    head = ("Your previous draft was rejected by an automated check. Rewrite it so it passes, "
            "keeping whatever was already correct.\n")
    if reason.startswith("contains banned phrase"):
        phrase = reason.split(":", 1)[-1].strip()
        return (head + f"It contained {phrase}. Remove that phrasing entirely, and do not address "
                "the reader or offer further help.")
    if reason.startswith("still contains a metric or company name"):
        offenders = [m.group(0) for m in list(METRIC_RE.finditer(draft))
                     + list(COMPANY_RE.finditer(draft))
                     if norm(m.group(0)) not in norm(question)]
        quoted = ", ".join(dict.fromkeys(offenders[:3])) or "a figure"
        return (head + f"It stated invented specifics ({quoted}). State NO figures at all - no "
                "percentage, ratio, multiplier, count or measurement, approximate or otherwise - "
                "and no company or product names the question did not mention. Describe the "
                "mechanism, the trade-off and how you would verify the result instead.")
    if reason == "too short":
        return head + "It was under 300 characters. Write 120-220 words of flowing prose."
    if reason == "too long":
        return head + "It was over 2,500 characters. Cut it to 120-220 words."
    if reason == "unfinished sentence":
        return head + "It did not end with terminal punctuation. Finish the last sentence."
    if reason == "duplicate of another generated answer":
        return head + "It repeated another answer from this batch. Answer this question specifically."
    return head + f"The check reported: {reason}."


def call_llm(question: str, subject: str, difficulty: str,
             api_key: str, base_url: str, model: str,
             retries: int = 8, timeout: int = 60, extra: str = "") -> str:
    user_content = USER_TMPL.format(subject=subject, difficulty=difficulty, question=question)
    if extra:
        user_content = f"{user_content}\n\n{extra}"
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_content},
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
            content = (data["choices"][0]["message"].get("content") or "").strip()
            if not content:
                # reasoning models can spend the whole budget thinking and return
                # nothing; retrying is cheaper than silently storing an empty answer
                last = "empty completion (no content in response)"
                time.sleep(1 + random.random())
                continue
            return content
        except urllib.error.HTTPError as e:
            body = e.read()[:600]
            text = body.decode("utf-8", "ignore")
            # keep the provider's own words: "per day" vs "per minute" decides whether the
            # run stops for the day or waits a few seconds, and a truncated repr hides that
            last = f"HTTP {e.code}: {text[:220]}"
            if e.code == 429 and ("per day" in text or "TPD" in text or "tokens per d" in text):
                STOP.set()  # daily quota gone: never retry, never risk a paid overage
                raise RuntimeError("daily quota exhausted: " + last)
            if e.code in (401, 403):
                # a revoked or wrong key will fail every remaining row identically
                STOP.set()
                raise RuntimeError("authentication failed: " + last)
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


def dry_run(rows: list[dict], targets: list[dict], only_template: bool = False,
            only_suspicious: bool = False, only_duplicated: bool = False,
            only_templated: bool = False, subject: str | None = None) -> None:
    mode = ("invented metrics / company names only" if only_suspicious
            else "duplicated answers only" if only_duplicated
            else "boilerplate answers only" if only_template
            else "answers containing a recurring (template) sentence" if only_templated
            else "boilerplate + duplicated answers")
    if subject:
        mode += f" | subject: {subject}"
    print(f"mode                 : {mode}")
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
    # Measured on this account: ~845 total tokens per answer (input + reasoning + output),
    # a day buys ~237 accepted answers. Output-only arithmetic understated this by ~3x.
    per_answer = 845
    print(f"free-tier days needed : ~{len(targets) * per_answer / 200_000:.1f} "
          f"at 237 accepted answers/day (~{per_answer} tokens each)")
    print(f"wall time             : ~{len(targets) / 8 / 60:.0f} min at 8 workers, ~1 req/s each")

    print("\n--- sample prompt ---")
    r = targets[0]
    print(SYSTEM)
    print()
    print(USER_TMPL.format(subject=r["subject"], difficulty=r["difficulty"], question=r["question"])[:400])


def resume_hint(args) -> str:
    """Command that continues this run, echoing the mode it was started with."""
    flag = ("--only-suspicious " if args.only_suspicious
            else "--only-duplicated " if args.only_duplicated
            else "--only-template " if args.only_template else "")
    return f"  python regenerate_answers.py {flag}--resume --out {args.out}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Regenerate weak answers with an LLM")
    ap.add_argument("--dry-run", action="store_true", help="show targets, cost and a sample prompt")
    ap.add_argument("--limit", type=int, help="only process the first N targets (pilot)")
    ap.add_argument("--only-template", action="store_true",
                    help="regenerate only the boilerplate answers (smaller, cheaper run)")
    ap.add_argument("--only-suspicious", action="store_true",
                    help="regenerate only answers that invent metrics or name companies")
    ap.add_argument("--only-templated", action="store_true",
                    help="rows whose answer contains a sentence that recurs across the dataset")
    ap.add_argument("--subject", metavar="NAME",
                    help="restrict targets to this subject (exact, case-insensitive)")
    ap.add_argument("--only-duplicated", action="store_true",
                    help="regenerate only answers whose text is shared with another row")
    ap.add_argument("--workers", type=int, default=8, help="parallel requests (default 8)")
    ap.add_argument("--out", default="answers_regenerated.csv", help="output CSV")
    ap.add_argument("--max-requests", type=int,
                    help="hard cap on API calls this run, corrective retries included")
    ap.add_argument("--release-quarantine", nargs="?", const="", metavar="TEXT",
                    help="clear quarantined rows (all, or those whose question contains TEXT) "
                         "and exit; use it when a row was held for the wrong reason")
    ap.add_argument("--quarantine-after", type=int, default=2, metavar="N",
                    help="stop targeting a question after N guard rejections (0 disables)")
    ap.add_argument("--retry", action="store_true",
                    help="opt in to one corrective second call per guard rejection "
                         "(only worth it if it recovers more than ~85%% of them)")
    ap.add_argument("--resume", action="store_true",
                    help="skip questions already present in --out (continue a stopped run)")
    ap.add_argument("--apply", metavar="FILE", help="merge a reviewed output CSV into the dataset")
    args = ap.parse_args()

    if args.release_quarantine is not None:
        held = load_quarantine()
        pattern = args.release_quarantine.strip().lower()
        kept = {q: info for q, info in held.items() if pattern and pattern not in q}
        released = len(held) - len(kept)
        save_quarantine(kept)
        scope = f"matching {pattern!r}" if pattern else "(all)"
        print(f"released {released:,} quarantined question(s) {scope}; "
              f"{len(kept):,} still held")
        return

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
        refused: list[tuple[str, str]] = []
        for r in rows:
            k = norm(r["question"])
            if k in by_q:
                new = by_q[k]
                # Validate again here: a run file may predate a guard, and merging is the last
                # chance to keep an invented figure out of the dataset.
                if not new:
                    refused.append((r["question"], "empty answer"))
                    continue
                if is_suspicious(new, r["question"]):
                    refused.append((r["question"], "invented specifics"))
                    continue
                r["answer"] = new
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
        if refused:
            print(f"refused {len(refused):,} row(s) at apply time:")
            for q, why in refused[:5]:
                print(f"  {why}: {q[:70]}")
        return

    targets = pick_targets(rows, only_template=args.only_template,
                           only_suspicious=args.only_suspicious,
                           only_duplicated=args.only_duplicated)
    quarantine = load_quarantine() if args.quarantine_after > 0 else {}
    quarantine_dirty = False
    if quarantine:
        blocked = {q for q, info in quarantine.items()
                   if info.get("n", 0) >= args.quarantine_after}
        held = [t for t in targets if norm(t["question"]) in blocked]
        if held:
            targets = [t for t in targets if norm(t["question"]) not in blocked]
            print(f"quarantine: {len(held):,} row(s) skipped after "
                  f"{args.quarantine_after} failed attempt(s)")
    if not targets:
        if args.only_suspicious:
            sys.exit("nothing to regenerate - no invented metrics or company names left")
        if args.only_duplicated:
            sys.exit("nothing to regenerate - no shared answer text left")
        sys.exit("nothing to regenerate - no templated or duplicated answers found")

    if args.only_templated:
        if not (args.only_template or args.only_suspicious or args.only_duplicated):
            targets = list(rows)      # the templated class is wider than TEMPLATES
        flags = templated_flags(rows)
        targets = [t for t in targets if flags.get(id(t))]
    if args.subject:
        wanted = args.subject.strip().lower()
        targets = [t for t in targets if (t.get("subject") or "").strip().lower() == wanted]
        if not targets:
            sys.exit(f"no targets in subject {args.subject!r}")
    if args.limit:
        targets = targets[: args.limit]

    if args.dry_run:
        dry_run(rows, targets, args.only_template, args.only_suspicious,
                args.only_duplicated, args.only_templated, args.subject)
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

    # --max-requests bounds API calls, retries included, so the loop counts them rather
    # than truncating the target list here.
    if not targets:
        if args.only_suspicious:
            print("nothing to regenerate - no invented metrics or company names left")
        else:
            print("nothing to regenerate - no templated or duplicated answers found")
        return
    print(f"regenerating {len(targets):,} answers with {model} via {base_url} ({args.workers} workers)")

    seen: set[str] = set()
    ok = rejected = failed = 0
    retried = recovered = 0
    request_budget = args.max_requests or len(targets)
    requests_used = 0
    mode = "a" if (args.resume and out_path.exists()) else "w"
    with open(out_path, mode, encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUT_COLS)
        if mode == "w":
            writer.writeheader()

        def record(row: dict, new: str, status: str) -> None:
            writer.writerow({**{k: row[k] for k in ("question", "subject", "difficulty")},
                             "old_answer": row["answer"], "new_answer": new,
                             "status": status})
        # Submit in small chunks rather than all at once: once the provider reports a daily
        # cap, every queued request would still fire against a dead budget. Chunking bounds
        # the waste to one round of workers.
        chunk_size = max(args.workers * 2, 1)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            queue = list(targets)
            meta: dict = {}  # future -> (row, retry_reason or None)

            inflight: set = set()

            def submit(row, extra="", prior=None):
                fut = pool.submit(call_llm, row["question"], row["subject"], row["difficulty"],
                                  api_key, base_url, model, extra=extra)
                meta[fut] = (row, prior)
                return fut

            # Drain even after STOP is set: requests already in flight were paid for, and
            # abandoning them silently is how a chunk loses rows it could still record.
            while queue or inflight:
                if not STOP.is_set():
                    # Top the pool up but never queue far ahead: if the daily cap fires, at
                    # most one round of requests is spent against a dead budget.
                    while queue and len(inflight) < chunk_size and requests_used < request_budget:
                        take = min(chunk_size - len(inflight), len(queue),
                                   request_budget - requests_used)
                        batch, queue = queue[:take], queue[take:]
                        requests_used += len(batch)
                        inflight.update(
                            submit(r, NO_METRICS if args.only_suspicious else "") for r in batch)
                if not inflight:
                    break
                finished, inflight = wait(inflight, return_when=FIRST_COMPLETED)
                for fut in finished:
                    r, prior = meta.pop(fut)
                    try:
                        new = fut.result()
                    except Exception as e:  # noqa: BLE001 - quota, auth, network
                        # A dead call still gets a row: the target stays visible as failed
                        # instead of vanishing, and the rest of the pool keeps draining.
                        failed += 1
                        record(r, "", f"error: {e}")
                        continue
                    if prior is None:
                        reason = validate(new, seen)
                        if not reason and is_suspicious(new, r["question"]):
                            reason = "still contains a metric or company name"
                        # Only failures a better prompt can fix, and only within budget.
                        retry_now = (reason and args.retry and is_retryable(reason)
                                     and not STOP.is_set()
                                     and requests_used < request_budget)
                        if retry_now:
                            # Submit the corrective retry rather than calling it here: done
                            # inline, the consumer thread blocks for the length of the call
                            # (or its 429 wait) and every other worker sits idle meanwhile.
                            retried += 1
                            requests_used += 1
                            inflight.add(submit(r, correction_for(reason, new, r["question"]),
                                                prior=reason))
                            continue
                    else:
                        again = validate(new, seen)
                        if not again and is_suspicious(new, r["question"]):
                            again = "still contains a metric or company name"
                        if again:
                            reason = f"{prior}; retry: {again}"
                        else:
                            reason = None
                            recovered += 1
                    if reason:
                        rejected += 1
                        record(r, new, f"rejected: {reason}")
                        if args.quarantine_after > 0:
                            key = norm(r["question"])
                            entry = quarantine.get(key, {"n": 0})
                            entry.update(n=entry.get("n", 0) + 1, reason=reason)
                            quarantine[key] = entry
                            quarantine_dirty = True
                    else:
                        seen.add(norm(new))
                        ok += 1
                        record(r, new, "ok")
                        if quarantine.pop(norm(r["question"]), None) is not None:
                            quarantine_dirty = True
                    written = ok + rejected + failed
                    if written % 100 == 0 or written == len(targets):
                        fh.flush()
                        retry_note = (f" retried={retried:,} recovered={recovered:,}"
                                      if args.retry else "")
                        print(f"  {written:,}/{len(targets):,}  ok={ok:,} "
                              f"rejected={rejected:,} failed={failed:,}{retry_note}")
            if STOP.is_set():
                fh.flush()
                print(f"  quota hit after {requests_used:,} request(s) - "
                      f"{len(queue) + len(inflight):,} target(s) left unsent")

    print(f"\nwrote {out_path.name}: ok={ok:,} rejected={rejected:,} failed={failed:,}"
          f" calls={requests_used:,}")
    if retried:
        print(f"corrective retries: {retried:,} spent, {recovered:,} recovered "
              f"({100 * recovered / retried:.0f}% success)")
    if args.quarantine_after > 0 and quarantine_dirty:
        save_quarantine(quarantine)
        held = sum(1 for v in quarantine.values()
                   if v.get("n", 0) >= args.quarantine_after)
        print(f"quarantine: {held:,} question(s) at {args.quarantine_after}+ rejection(s)")

    if STOP.is_set():
        print("STOPPED on an unrecoverable provider error (quota or credentials) — nothing was charged.")
        print("Continue later with:")
        print(resume_hint(args))
    else:
        print("Review a sample, then run: python regenerate_answers.py --apply", args.out)


if __name__ == "__main__":
    main()
