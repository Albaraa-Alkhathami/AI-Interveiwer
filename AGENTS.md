# AGENTS.md

Instructions for coding agents working in this repository. Read this before changing
anything; the rules below exist because each one was learned the hard way.

## What this project is

Two deliverables in one folder:

1. **`combined_dataset.csv`** — 23,954 interview questions with reference answers, 46 subjects,
   3 difficulty levels, 138/138 subject × difficulty cells covered, schema
   `prompt, question, answer, subject, difficulty, data_flag`.
2. **A semantic search prototype** (`search_app.py`) that embeds questions locally with
   `BAAI/bge-small-en-v1.5` (ONNX, CPU) and returns the top-k answers by cosine similarity,
   filtered by subject and difficulty.

Supporting scripts improve the dataset in reviewable batches. Current limitations are
documented in `DATA_LIMITATIONS.md`; outstanding work is in `NEXT_SESSION.md`.

## Non-negotiables

- **Never write an API key into a file, a commit, or a chat message.** Keys come from
  `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` in the environment. Do not read,
  print or echo a key file.
- **Never retry a `429` whose body says `per day`.** That is the free tier's hard cap; retrying
  risks a paid overage. Set `STOP` and end the run. Per-minute (`TPM`) limits are different —
  wait the hinted `try again in Xs` and retry.
- **No fabricated specifics.** Generated answers must not state benchmark numbers, metrics,
  multipliers, company names or dates. Describe the mechanism and trade-off instead.
- **ASCII punctuation only** in answers (curly quotes, non-breaking hyphens and em dashes are
  normalised away). Exceptions kept deliberately: `…` and maths symbols such as `∑ × ³ ∞ → λ`.
- **Scripts resolve paths from `__file__`** (`BASE = Path(__file__).resolve().parent`) so they
  work when run from anywhere. Never hardcode `C:\Users\...`.

## Answer style contract

Every generated answer must satisfy all of these; `validate()` in `regenerate_answers.py`
enforces the mechanical ones:

| Rule | Detail |
|---|---|
| Length | 300–2,500 characters, target 120–220 words |
| Voice | First person, as the candidate ("I start by…") |
| Format | Plain prose. No markdown, bold, headings, bullets or tables |
| Opening | No filler ("Certainly.", "Sure.", "Of course.") |
| Ending | Terminal punctuation; no assistant chatter ("Let me know if you need anything else") |
| Content | No invented metrics, company names, dates or results |
| Specificity | Name the technique, mechanism, trade-off or debugging step |

## Quota discipline (Groq free tier)

- **8,000 tokens/minute** and **200,000 tokens/day** for `openai/gpt-oss-20b`.
  One 220-word answer costs roughly 600–900 tokens, so a day yields **~215 answers**, not the
  1,000 the request counter suggests. The per-day token bucket binds first.
- `gpt-oss-20b` is a **reasoning model**. For JSON generation pass
  `reasoning_effort: "low"` — otherwise it can spend the whole `max_tokens` on hidden
  reasoning and return `content: ""` with `finish_reason: "length"`. That failure is silent:
  it looks like "0 candidates", not like an error.
- **An empty completion is a failure**, never an empty answer. Both scripts retry it.
- Always pass `--max-requests`/`--budget` so a run cannot outlive its allowance; it now counts
  corrective retries too, so the allowance means API calls, not targets.
- **A corrective retry must beat the alternative to be worth its call.** With a fixed call
  budget, retrying a rejection (rate ~15%) spends a call that would otherwise have gone to a
  fresh target, which succeeds 85% of the time. The retry therefore pays only if it recovers
  more than ~85% of rejections; below that, leaving `--retry` off yields more accepted answers.
  Every run prints `corrective retries: N spent, M recovered`, so the two modes can be compared
  on the same dataset instead of argued about.
- **Quarantine what keeps failing.** `--quarantine-after N` (default 2) counts guard
  rejections per question in the gitignored `quarantine.json` and stops targeting a row at
  the threshold; a later success clears it. Some questions cannot satisfy the guard by
  construction ("what is folding?" invites a two-line answer that the 300-character rule
  rejects), and re-targeting them daily buys the same rejection. Release rows with
  `--release-quarantine` (all) or `--release-quarantine TEXT` (questions containing TEXT);
  that command exits without running a batch.
- **Retry only what a prompt can fix.** `RETRYABLE` covers invented specifics, banned phrasing,
  length and unfinished sentences. A duplicate of another generated answer is excluded: the
  model cannot see the answer it collided with, so asking again returns the same text.
- **A cheap probe proves authentication, not budget.** A 4-token request can be served on the
  last few hundred tokens of an exhausted day and still return 200; the first real answer then
  fails with `per day`. Do not conclude "the cap has reset" from a probe — this has cost two
  runs. Only a completed answer, or the `x-ratelimit-remaining-tokens` header sitting near the
  full 8k, is evidence of headroom.
- Generators write a reviewable CSV; `--apply` merges it. Use `--resume` to continue a stopped
  run — it skips rows already marked `ok`.
- `generate_questions.py` **appends** to its output file, so merging two runs can repeat rows.
  `--apply` dedupes against the dataset; do not blind-append.

## Scripts

| Script | Purpose | Key flags |
|---|---|---|
| `search_app.py` | validate, embed, search | `--validate`, `--rebuild`, `-q`, `-s`, `-d`, `-k`, `--interactive` |
| `regenerate_answers.py` | rewrite weak answers | `--only-template`, `--only-suspicious`, `--only-duplicated`, `--only-templated`, `--subject`, `--resume`, `--max-requests` (counts retries), `--retry` (opt-in), `--workers`, `--dry-run`, `--apply` |
| `generate_questions.py` | fill coverage gaps | `--plan`, `--auto`, `--budget`, `--subject`, `--difficulty`, `--count`, `--apply` |
| `dedupe_answers.py` | remove near-duplicate answers, one row per cluster, no model | `--apply` (dry run otherwise), `--scope question\|subject\|answer`, `--threshold`, `--question-threshold`, `--slot-min-count`, `--show` |
| `cluster_duplicates.py` | measure paraphrase/opening duplication, no model | `--only questions\|answers\|openings`, `--thresholds`, `--opening-min-count`, `--slot-min-count` (25 = count slot-filled variants as one template), `--answer-prefix`, `--json` |
| `clean_answers.py` | mechanical answer fixes, no model | dry run by default, `--apply`, `--show N` |
| `audit_dataset.py` | read-only quality audit, no model | `--question/--answer/--subject/--difficulty`, `--json`; exits 1 on blockers |
| `build_final_workbook.py` | regenerate `final_dataset.xlsx` from the CSV | none; needs `openpyxl`, writes next to the script |

## Post-edit checklist

Run this after **any** dataset or script change:

1. `python -m pytest tests -q` — the full suite, no network and no API key needed. If you
   add tests, update this file rather than letting the count drift.
   `python audit_dataset.py combined_dataset.csv` runs before any token spend and again after a
   batch; it exits non-zero on empty fields, duplicate questions or invented specifics.
2. `python clean_answers.py` (dry run) — confirm you did not introduce chatter, filler,
   markdown or typographic punctuation.
3. Rebuild the workbook if `combined_dataset.csv` changed: `python build_final_workbook.py`
   (needs `openpyxl`). It prints rows, subject count and the saved path.
4. **Index rebuild is usually unnecessary.** The cache fingerprint hashes only
   `subject + question`, so answer-only edits keep the existing `.npy`. Changing questions,
   subjects or row order **does** invalidate it: `python search_app.py --rebuild` (~10 min).
5. Update the figures in `DATA_LIMITATIONS.md` if counts changed — they are measured, not
   approximated, and stale numbers there are treated as bugs.
6. Commit with a message explaining *why*. Never commit `embeddings/`, `*.log`, `*.bundle`,
   `.qkeep/` or scratch CSVs — `.gitignore` covers them.

## Data rules

- One row per question text; no exact duplicates, no empty required fields.
- `subject` filtering is an **exact, case-insensitive** match. Substring matching is a bug: it
  let `-s Java` return JavaScript rows, and there is a test guarding that.
- `difficulty` is exactly `Easy` / `Medium` / `Hard`.
- `data_flag` carries `limited data (<20 questions)` for thin subjects. Do not invent other
  values; add new columns only with the user's agreement, since the five-column schema is part
  of the brief.
- 1,086 rows still share their answer text with another row — a known defect, not intent.
- A figure or company name **supplied by the question** is context, not invention. Both
  `is_suspicious()` and `audit_dataset.py` ignore matches the question already contains; without
  that, rewrite targets loop forever because the prompt asks for what the guard bans.

## Testing conventions

- `tests/test_dataset.py` — schema, uniqueness, coverage, exact filters, index fingerprint,
  cleaner guards.
- `tests/test_generation.py` — both generation scripts via a scripted fake `urllib.request.urlopen`;
  `time.sleep` is patched out. Retry, quota-stop and empty-completion paths are covered.
- **Write the failing test before fixing a bug in these paths.** Two real defects (a
  case-mismatched banned-phrase check and empty completions treated as success) hid for days
  precisely because nothing exercised them.

## How to respond to the user

The user's standing preferences for this project (agreed 14 Nov 2026):

1. **Keep tables and headers.** Dense measurement reports are wanted in tabular form; do not
   flatten them into prose.
2. **For every decision put to the user, use this two-row table**, signs in the row header and
   positives before negatives inside each cell:

   ```markdown
   |          | If we do it                                   | If we don't                                  |
   |----------|-----------------------------------------------|----------------------------------------------|
   | **(+)**  | positive aspect<br>positive aspect            | positive aspect<br>positive aspect           |
   | **(-)**  | negative aspect                               | negative aspect<br>negative aspect           |
   ```

   Why a table and not a bullet list: in Markdown a leading `+`, `-` or `*` is a list marker, so
   a bullet list renders every point with the same round dot and the sign is lost. Table row
   headers keep `(+)` and `(-)` visible. Separate multiple points in a cell with `<br>`.

   Add a short prose paragraph after the table with the recommendation and its reason — the table
   states the trade-off, it does not make the choice.

   Every branch carries both signs — a branch with only upsides means the trade-off was not
   found yet. Keep the points distinct between branches; do not mirror the same point twice.
3. **Do not act as a yes-man.** If the user is wrong, say so plainly and explain the error. If a
   request would damage the project, argue against it before doing it. Agreement is not a
   deliverable.

4. **Do not ask before doing routine work.** When work from the plan remains and nothing blocks
   it, carry on and report afterwards. Stop only for actions that are destructive or hard to
   undo, spend money, or publish something externally — and for genuinely missing information
   (a credential, a choice between incompatible goals). "Shall I proceed?" is not a question to
   ask when the answer was already agreed.

Rule 2 applies to genuine choices with a trade-off. Do not manufacture a do/do-not section for
routine, reversible steps — that is filler, and it buries the real decisions.

## Known gaps

- `final_dataset.xlsx` is committed (≈10 MB per version). Rebuilding it rewrites that blob, so
  rebuild once after a batch of dataset changes rather than after every apply.
- `regenerate_answers.py` and `generate_questions.py` have no end-to-end test against a real
  endpoint; everything is mocked.
- `audit_dataset.py` is also embedded in an account skill (`interview-dataset-style`) so it is
  available outside this repo; keep the two copies in step if you change it. Synced 16 Nov 2026,
  before the markdown-exponent fix — the skill's copy still flags `x ** 2` as bold, so re-save it
  if that matters for other projects.
- The repo history was rewritten on 14 Nov 2026 to drop duplicate 60 MB dataset blobs. A safety
  bundle exists locally (`git-history-backup-2026-11-14.bundle`, gitignored). Adding the dataset
  as a fresh commit costs ~27 MB packed, so prefer release assets over repeated commits.
