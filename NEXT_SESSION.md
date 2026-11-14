# Next session — priority order

Agreed 14 Nov 2026. **Improving existing rows comes before generating new ones**, so the
remaining daily token budget is spent in this order and generation only gets the leftovers.

Order: (1) fabricated specifics → (2) chatter and short answers → (3) verify the two validator
fixes on live traffic → (4) generation.

## 1. Fabricated specifics (first)

76 answers carry invented metrics ("we compressed the model size by 4.3x", "60% less
memory") and 31 name real companies (Google, Amazon, Netflix…). These came from
research-abstract source rows and are the highest-risk content in the dataset: they read as
verified results.

Plan: reuse the `regenerate_answers.py` pipeline with a selector for exactly these rows, so
the 220-word rewrites describe *technique and trade-offs* rather than results. The prompt
already forbids invented metrics and company names; the selector is the missing piece.

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

## 4. Generation with whatever budget remains

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
