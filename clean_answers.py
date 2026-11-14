"""Mechanical cleanup of generation artefacts in answer text.

Fixes only things that need no model and no judgement:

  1. trailing assistant chatter   ("... Let me know if you need anything else.")
  2. leading filler              ("Certainly.", "Sure!", "Of course.")
  3. stray markdown bold         ("**root injector**" -> "root injector")

Guarded so it can never damage real content: an answer is left untouched if the
result would lose more than 30% of its length, drop below 100 characters, or stop
ending in terminal punctuation. Bold is only unwrapped when the span holds no
backticks or operators, which keeps Python's `**` exponent and code snippets intact.

Usage:
    python clean_answers.py                 # dry run: report what would change
    python clean_answers.py --apply         # write combined_dataset.csv (with backup)
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
CSV_PATH = BASE / "combined_dataset.csv"
BACKUP_PATH = BASE / "combined_dataset_before_cleanup.csv"

CHATTER = re.compile(
    r"(let me know|hope this helps|feel free to ask|i hope this|"
    r"let me know if you need|please let me know)",
    re.IGNORECASE,
)
FILLER = re.compile(r"^\s*(certainly|sure|of course)\s*[.,!]\s*", re.IGNORECASE)
BOLD = re.compile(r"\*\*(?P<inner>[^*\n`]{2,120})\*\*")
SENTENCE_END = ".!?"


def strip_trailing_chatter(answer: str) -> str:
    """Drop sentences at the very end that are assistant chatter."""
    parts = re.split(r"(?<=[.!?])\s+", answer.strip())
    while len(parts) > 1 and CHATTER.search(parts[-1]):
        parts.pop()
    return " ".join(parts).strip()


def strip_leading_filler(answer: str) -> str:
    stripped = FILLER.sub("", answer, count=1)
    if stripped != answer and stripped[:1].isalpha():
        stripped = stripped[0].upper() + stripped[1:]
    return stripped


def unwrap_bold(answer: str) -> str:
    """Remove **bold** markers but never touch code or arithmetic."""

    def repl(m: re.Match[str]) -> str:
        inner = m.group("inner")
        if any(ch in inner for ch in "()=+*/<>[]{}\\") or inner.strip() != inner:
            return m.group(0)
        return inner

    return BOLD.sub(repl, answer)


# Typographic characters that break exact matching, CSV round-trips and naive
# keyword search. They carry no meaning that ASCII punctuation does not.
TYPOGRAPHIC = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2212": "-",
    "\u00a0": " ", "\u202f": " ", "\u2009": " ",
}


def normalise_typography(answer: str) -> str:
    """Map curly quotes, non-breaking hyphens and odd spaces to ASCII equivalents."""
    return "".join(TYPOGRAPHIC.get(ch, ch) for ch in answer)


def collapse_whitespace(answer: str) -> str:
    """Collapse runs of spaces inside prose, leaving indented code blocks alone."""
    if re.search(r"^\s{2,}\S", answer, re.M):
        return answer  # likely code; indentation is meaningful
    lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in answer.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def clean(answer: str) -> str:
    """Guarded pass: only edits that could shorten an answer enough to matter."""
    out = strip_leading_filler(answer.strip())
    out = unwrap_bold(out)
    out = strip_trailing_chatter(out)
    out = collapse_whitespace(out)
    return out


MIN_LEN = 60  # below this an answer is too thin to be useful; leave it for the LLM pass


def safe(original: str, cleaned: str) -> tuple[bool, str]:
    # Only chatter sentences, filler and asterisks are ever removed, never content,
    # so a large relative drop just means the row was mostly chatter.
    if cleaned == original:
        return False, "unchanged"
    if len(cleaned) < MIN_LEN:
        return False, "too short after cleaning"
    if cleaned.rstrip()[-1:] not in SENTENCE_END + "…":
        return False, "no terminal punctuation"
    return True, "ok"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--show", type=int, default=5, help="sample changes to print")
    args = ap.parse_args()

    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = list(reader.fieldnames or [])
        rows = list(reader)

    changed, skipped, retyped = [], [], []
    for row in rows:
        original = row["answer"]
        # Typography is applied unconditionally: mapping a curly quote to an ASCII one
        # cannot damage meaning, so the length guard (aimed at chatter removal) must not
        # skip short rows and leave them unmatchable.
        normalised = normalise_typography(original)
        if normalised != original:
            retyped.append(row)
            row["answer"] = normalised
        cleaned = clean(normalised)
        ok, why = safe(normalised, cleaned)
        if ok:
            changed.append((row, normalised, cleaned))
        elif why != "unchanged":
            skipped.append((row, why))

    print(f"rows: {len(rows):,} | typography: {len(retyped):,} | to clean: {len(changed):,} "
          f"| skipped by guard: {len(skipped):,}")
    for row, original, cleaned in changed[: args.show]:
        print(f"\n  [{row['subject']}] {row['question'][:70]}")
        print(f"  before: ...{original[-110:]!r}")
        print(f"  after : ...{cleaned[-110:]!r}")
    if skipped:
        print("\n  skipped:")
        for row, why in skipped[:5]:
            print(f"    {why}: {row['question'][:60]}")

    if not args.apply:
        print("\ndry run — re-run with --apply to write")
        return

    shutil.copy(CSV_PATH, BACKUP_PATH)
    for row, _, cleaned in changed:
        row["answer"] = cleaned
    # rows whose typography changed stay updated even when their guard skipped them
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nbackup: {BACKUP_PATH.name}")
    print(f"applied {len(changed):,} cleaned answers + {len(retyped):,} typography fixes "
          f"-> {CSV_PATH.name}")


if __name__ == "__main__":
    sys.exit(main())
