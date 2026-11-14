# Next session — priority order

Agreed 14 Nov 2026. **Improving existing rows comes before generating new ones**, so the
remaining daily token budget is spent in this order and generation only gets the leftovers.

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

## 3. Generation with whatever budget remains

- General Computer Science (12 questions) and Version Control (17) are still flagged
  `limited data (<20 questions)`.
- Run `generate_questions.py --plan` first; it recomputes the deficits from the current file.

## Notes for the run

- Free tier: **8,000 tokens/minute** and **200,000 tokens/day**. Expect ~215 answers per day,
  not the 330 an earlier estimate assumed.
- `generate_questions.py` appends to its output CSV. If two runs are merged, dedupe before
  `--apply` (this caused 45 duplicate rows on 14 Nov).
- `--resume` skips rows already marked `ok` in the output file, so a stopped run is safe to
  continue.
- After applying: `build_final_workbook.py`, then `python -m pytest tests -q`. Only question
  or subject changes require `search_app.py --rebuild`; answer-only edits keep the cache.
