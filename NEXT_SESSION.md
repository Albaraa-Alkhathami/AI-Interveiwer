# Next session — priority order

Agreed 14 Nov 2026. **Improving existing rows comes before generating new ones**, so the
remaining daily token budget is spent in this order and generation only gets the leftovers.

Order: (1) fabricated specifics → (2) chatter and short answers → (3) verify the two validator
fixes on live traffic → (4) shared answers → (5) generation.

## 1. Fabricated specifics (in progress — 18 of 108 done)

108 answers carry invented metrics ("we compressed the model size by 4.3x", "60% less
memory") or name real companies (Google, Amazon, Netflix…). Highest-risk content: it reads as
verified results.

Tooling is in place: `--only-suspicious` selects exactly these rows, appends `NO_METRICS` to the
prompt, and **rejects** any rewrite that still contains a figure or company name.

**Remaining: 94 rows.** Note that 4 of the first 18 rewrites slipped a hedged figure
("about 30%") because the question itself asks "how much did you improve" — the new rejection
rule catches those, so expect a higher reject rate and rerun until the count stops falling.

```bash
export OPENAI_API_KEY=... OPENAI_BASE_URL=https://api.groq.com/openai/v1 OPENAI_MODEL=openai/gpt-oss-20b
python regenerate_answers.py --only-suspicious --resume --workers 2 --max-requests 250 --out answers_suspicious.csv
python regenerate_answers.py --apply answers_suspicious.csv
```

## 2. Chatter, short answers, remaining boilerplate

Ordered by how visible the defect is:

| Target | Rows | Why |
|---|---:|---|
| Remaining boilerplate | 1,059 | Most visible: "A good answer should address the requirements…" |
| Under 100 characters | 91 | Placeholders, not explanations (34 were too thin for the cleaner) |
| Mid-text chatter | 28 | "Feel free to ask…" left after the mechanical pass |
| Leading filler | 9 | "Certainly." openers |

`clean_answers.py` cannot fix these — they need generated text.

## 3. Confirm the two validator/completion fixes on live traffic

Both are fixed and unit-tested (commit `b85a0b9`), with mocked HTTP only — they have not
yet been exercised against the real API. Watch for these on the first real run:

| Fix | What to look for |
|---|---|
| Banned-phrase check now compares lowercased patterns | No accepted answer contains "A good answer should address the requirements…" or "Certainly!". A non-zero `rejected: contains banned phrase` count is now *expected*, not a bug. |
| Empty completion is a retryable failure | `failed` counts may rise and `ok=0` should never be reported for a cell that actually returned nothing. Log lines like `empty completion (no content in response)` are the guard working. |

If either misbehaves, the failing case belongs in `tests/test_generation.py` first.

## 4. Shared answers (1,362 rows)

**1,362 rows share their answer text with another row** — 265 distinct answers reused across
different questions. The question-level dedupe cannot see this: every question is unique, but
the answer next to it may have been copied from an unrelated one. This is worse than the
boilerplate rows, because a templated answer reads as generic while a shared answer reads as
specific and answers the wrong question.

Targeted by `regenerate_answers.py` **without** `--only-template` (its default mode is
boilerplate + duplicated answers). Re-run `--dry-run` first — the count now includes the
remaining boilerplate, so the target list is larger than 1,362.

## 5. Generation with whatever budget remains

- General Computer Science (12 questions) and Version Control (17) are still flagged
  `limited data (<20 questions)`.
- Run `generate_questions.py --plan` first; it recomputes the deficits from the current file.

## Repo size — **done 14 Nov 2026**

History previously stored 7 copies of `combined_dataset.csv` (≈421 MB uncompressed) plus old
workbook snapshots, for a **155 MB** `.git`.

What was done, without touching working files (all checksums verified identical before/after):

```bash
git bundle create git-history-backup-2026-11-14.bundle --all   # 81 MB safety net
python -m pip install git-filter-repo
git filter-repo --path combined_dataset.csv --path final_dataset.xlsx --invert-paths --force
git remote add origin https://github.com/Albaraa-Alkhathami/AI-Interveiwer.git
git push --force origin main
```

Result: **155 MB → 27 MB**, 15 commits of code history retained, and the current dataset kept
as one snapshot commit so a fresh clone still contains the data.

Consequences to remember:

- History no longer contains superseded dataset versions — `git log` cannot recover an older
  `combined_dataset.csv`. The 81 MB bundle (`git-history-backup-2026-11-14.bundle`, gitignored)
  holds the pre-rewrite history if an old version is ever needed.
- Any other clone of this repo must be re-cloned; the old commits no longer exist remotely.
- Every future dataset commit adds ~60 MB again (≈15–35 MB packed). If that becomes a problem,
  publish the dataset as a GitHub Release asset instead of committing it, and keep it gitignored.
- Never run `git gc --prune=now`: unreachable objects may hold abandoned work.

## Notes for the run

- Free tier: **8,000 tokens/minute** and **200,000 tokens/day**. Expect ~215 answers per day,
  not the 330 an earlier estimate assumed.
- `generate_questions.py` appends to its output CSV. If two runs are merged, dedupe before
  `--apply` (this caused 45 duplicate rows on 14 Nov).
- `--resume` skips rows already marked `ok` in the output file, so a stopped run is safe to
  continue.
- After applying: `build_final_workbook.py`, then `python -m pytest tests -q`. Only question
  or subject changes require `search_app.py --rebuild`; answer-only edits keep the cache.
