# Next session — priority order

Updated 15 Nov 2026. **Improving existing rows comes before generating new ones**, so the
remaining daily token budget is spent in this order and generation only gets the leftovers.

Before spending anything: `python audit_dataset.py combined_dataset.csv` gives the current
defect counts in one pass (it exits non-zero while invented specifics remain).

## Where the dataset stands (measured 15 Nov 2026)

| Metric | Value |
|---|---:|
| Rows | 23,954 |
| Subjects | 46 |
| Coverage | 138/138 cells (Easy/Medium/Hard) |
| Subjects under 20 questions | 2 (General Computer Science 12, Version Control 17) |
| Invented specifics (metrics / companies) | 85 (54 / 31) |
| Boilerplate answers | 1,059 |
| Duplicated answer texts | 264 distinct texts across 1,360 rows; 1,150 are rewrite targets |
| Default-mode targets | 2,209 (9.2%) |
| Answers under 100 characters | 90 |
| Assistant chatter / filler openers | 32 / 12 |
| Markdown leakage / typographic punctuation | 3 / 0 |

## 1. Fabricated specifics — 85 rows remaining

Highest-risk content: an invented figure ("we compressed the model size by 4.3x") reads as a
verified result. `--only-suspicious` appends `NO_METRICS` to the prompt and **rejects** any
rewrite that still contains a figure or company name, so expect a reject rate: those rows need
a second pass, which is normal, not a failure.

```bash
export OPENAI_API_KEY=... OPENAI_BASE_URL=https://api.groq.com/openai/v1 OPENAI_MODEL=openai/gpt-oss-20b
python regenerate_answers.py --only-suspicious --resume --workers 2 --max-requests 250 --out answers_suspicious2.csv
python regenerate_answers.py --apply answers_suspicious2.csv
python regenerate_answers.py --only-suspicious --dry-run    # target count must fall
```

Start there rather than at (2): these are the only rows that can mislead a reader into thinking
a fabricated result is real, and they are 85 rows, not 2,000.

## 2. Chatter, short answers, remaining boilerplate

| Target | Rows | Why it matters |
|---|---:|---|
| Boilerplate | 1,059 | "A good answer should address the requirements…" — visible filler |
| Under 100 characters | 90 | Placeholders, not explanations |
| Mid-text chatter | 32 | "Feel free to ask…" surviving the mechanical pass |
| Leading filler | 12 | "Certainly." openers |

`clean_answers.py` cannot fix these — they need generated text. Run default mode, which targets
boilerplate plus duplicated answers:

```bash
python regenerate_answers.py --only-template --resume --workers 2 --max-requests 250 --out answers_template.csv
```

## 3. Shared answers — 1,360 rows (1,150 rewrite targets)

Different questions carrying identical answer text. Question-level dedupe cannot see this: each
question is unique, so the defect is invisible to a uniqueness check while the answer next to it
belongs to an unrelated question. Worse than boilerplate, because a templated answer reads as
generic whereas a shared answer reads as specific and answers the wrong thing.

Covered by default mode (step 2), so this needs no separate command — just expect the target list
to stay large until step 2 clears it.

## 4. Generation — only with leftovers

- `generate_questions.py --plan` recomputes deficits from the current file.
- General Computer Science (12) and Version Control (17) are the only cells under 20 questions.
- `--apply` dedupes against the dataset; `--resume` skips rows already marked `ok`.

## 5. Live-traffic checks still owed

Both validator fixes (`b85a0b9`) are unit-tested against mocked HTTP and have never met the real
API. On the first run, watch for:

| Behaviour | Expected on live traffic |
|---|---|
| Banned-phrase check now lowercases the pattern | A non-zero `rejected: contains banned phrase` count is the fix working, not a bug |
| Empty completion is a retryable failure | `empty completion (no content in response)` lines, and never `ok` for a cell that returned nothing |

If either misbehaves, add the failing case to `tests/test_generation.py` before touching the script.

## Decisions already taken (do not reopen)

| Decision | Outcome |
|---|---|
| Normalise the ~640 American spellings in a British-dominant corpus | **Declined** 14 Nov 2026 — the corpus stays as it is |
| Model for rewriting | `openai/gpt-oss-20b` with `reasoning_effort: "low"`; the rejection guard protects quality, so throughput wins |
| Dataset in git history | History rewritten 14 Nov 2026 (155 MB → 27 MB) to drop duplicate blobs; do not re-add old versions |

## Run notes

- Free tier: **8,000 tokens/minute**, **200,000 tokens/day** → ~215 answers/day, not the 330 an
  earlier estimate assumed. The per-day token bucket binds first.
- **Never retry a `429` that says `per day`** — stop the run; resume tomorrow with `--resume`.
- `generate_questions.py` appends to its output CSV; two merged runs duplicate rows (this caused
  45 duplicate rows on 14 Nov).
- After each apply: `python -m pytest tests -q`, then `python audit_dataset.py combined_dataset.csv`,
  then rebuild the workbook. Only question, subject or row-order changes need
  `search_app.py --rebuild` (~10 min); answer-only edits keep the embedding cache.
- Stale figures in `DATA_LIMITATIONS.md` are treated as bugs — update them whenever counts move.
