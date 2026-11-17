# Next session — priority order

Updated 15 Nov 2026. **Improving existing rows comes before generating new ones**, so the
remaining daily token budget is spent in this order and generation only gets the leftovers.

Before spending anything: `python audit_dataset.py combined_dataset.csv` gives the current
defect counts in one pass (it exits non-zero while invented specifics remain).

**Revised order, 15 Nov 2026:** take **§3 shared answers before the remaining §2 boilerplate**.
A shared answer misleads the reader by answering the wrong question; boilerplate merely bores.
The section numbers are kept so older notes still refer to the same work.

## Where the dataset stands (measured 15 Nov 2026)

| Metric | Value |
|---|---:|
| Rows | 23,954 |
| Subjects | 46 |
| Coverage | 138/138 cells (Easy/Medium/Hard) |
| Subjects under 20 questions | 2 (General Computer Science 12, Version Control 17) |
| Invented specifics (metrics / companies) | **0** (was 85) |
| Boilerplate answers | 799 (was 1,058) |
| Duplicated answer texts | 167 distinct texts across 1,086 extra rows; 1,253 rewrite targets |
| Default-mode targets | 2,015 (8.4%) |
| Answers under 100 characters | 74 |
| Assistant chatter / filler openers | 32 / 12 |
| Markdown leakage / typographic punctuation | 3 / 0 |

## 1. Fabricated specifics — **done 15 Nov 2026**, 0 remaining

85 rows were regenerated in one run: **77 accepted, 6 rejected by the guard, 2 failed**. After
applying and cleaning, the re-audit reports 0. The 8 unrewritten rows turned out to be false
positives — the question itself supplies the term ("Magento connected with Google Analytics",
"reduce response time by at least 30%") — so `is_suspicious()` and `audit_dataset.py` now ignore
any figure or company name the question already contains. Regenerating those would have looped
forever, since the prompt asked for an answer the guard then banned.

What this cost: ~77 answers of the daily budget (roughly 60k tokens). If the pool is ever
reopened, the same command applies:

```bash
export OPENAI_API_KEY=$(cat .key) OPENAI_BASE_URL=https://api.groq.com/openai/v1 OPENAI_MODEL=openai/gpt-oss-20b
python regenerate_answers.py --only-suspicious --resume --workers 2 --max-requests 250 --out answers_suspicious2.csv
python regenerate_answers.py --apply answers_suspicious2.csv
python clean_answers.py && python clean_answers.py --apply   # new text reintroduces typography
```

Watch out: the model emits non-breaking hyphens and curly apostrophes even when the prose is
otherwise clean, so **the cleaner must run after every apply** or the typography count regresses.

## 2. Chatter, short answers, remaining boilerplate — **in progress**

15 Nov 2026: 188 boilerplate answers rewritten in one capped run (4 rejected, 8 failed), taking
the count from 1,058 to **870**. The run then stopped cleanly on the daily token cap — nothing was
charged. Resume tomorrow with the same command; `--resume` skips the 188 already done.

| Target | Rows | Why it matters |
|---|---:|---|
| Boilerplate | 799 | "A good answer should address the requirements…" — visible filler |
| Under 100 characters | 89 | Placeholders, not explanations |
| Mid-text chatter | 32 | "Feel free to ask…" surviving the mechanical pass |
| Code fences | 4 | ```` ``` ```` blocks (Vuex, React forwardRef, useEffect, styled-components) — need a prose rewrite, not a mechanical strip |
| Leading filler | 12 | "Certainly." openers |

`clean_answers.py` cannot fix these — they need generated text. Run default mode, which targets
boilerplate plus duplicated answers:

```bash
python regenerate_answers.py --only-template --resume --workers 2 --max-requests 250 --out answers_template.csv
```

## 3. Shared answers — 1,265 rows (1,145 rewrite targets)

**16 Nov 2026, first attempt:** the daily cap was already spent. 200 requests returned 4 ok and
196 `429 ... tokens per day`, and the run stopped cleanly. Nothing was applied, so the dataset is
unchanged. A new `--only-duplicated` mode now targets exactly these rows (1,526 targets, 381 of
them also boilerplate) instead of dragging the boilerplate pool in:

```bash
python regenerate_answers.py --only-duplicated --resume --workers 2 --max-requests 200 --out answers_duplicated.csv
```

**Lesson worth keeping:** a 200 response on a probe proves *some* budget remains, not that a day's
budget is available — on 16 Nov the probe succeeded and the very next run died after 4 answers.
Groq exposes only the per-minute remainder in headers, so the only honest signal is the first
real run; do not announce "quota is back" on a probe.

**17 Nov, first full budget:** 200 requests (169 accepted) on shared answers, then 76 more on
boilerplate before `429 ... tokens per day`. So the documented 200k daily cap is real and equals
roughly **270 answer rewrites per day**. Shared answers 1,250 → 1,086 extra rows (167 texts),
boilerplate 870 → 799, thin answers 89 → 74. Chat-bubble chatter and filler also fell as a side
effect (32 → 27, 12 → 10).

**17 Nov, corrective retry added:** a guard rejection now gets exactly one retry whose prompt
quotes the failing rule (and the offending figure). It is **off by default** and only runs with
`--retry`. Measure it on one real run with `--retry` before enabling it anywhere else: keep it
only if `recovered / spent` exceeds ~85%, otherwise the call is better spent on a fresh target.
Retries are submitted to the same pool, not called from the consumer thread: an A/B with 8
targets, 2 workers and a 50 ms fake latency took 0.54 s when the retry blocked the consumer
and 0.41 s (ideal 0.40 s) once pooled. The overlap is now asserted without timing: a
the test has the retry record which thread it ran on and holds a second target open until the
retry starts, parametrised over 2 and 4 workers so the queue depth cannot matter. It fails
immediately when the retry is made synchronous again and passes on the pooled loop. A call that dies still writes its row, and the loop
drains in-flight work after a quota stop instead of abandoning the chunk.

**17 Nov, slot-aware recount:** treating slot-filled sentences as templates lowers the dedupe
figure from 5,074 to **746-3,595 rows**, 2,063 at the default of 25 recurrences, and raises
fully-templated answers from 14,708 to 16,950-20,095. The threshold matters more than any
single number: skeletons recurring 10+ times leave 746 reducible rows, 25+ leaves 2,063, 50+
leaves 3,595, each step moving ~1,500 answers into the templated class. 50 is too strict -
the 10-24 band still holds plainly generated templates (108 skeletons / 1,675 instances,
"That breaks into these steps: (1) pin the contract down in writing...") - and 10 is too
loose, since a sentence reused by ten rows may be a correct answer rather than a skeleton.
Read the range as **746-2,063**: unnormalised lowercase slots ("an appropriate angular
technique") stay in the body, so those rows read as specific duplicates and inflate the
figure, which means the true value sits at or below the 25 default. The slot is real: `sentence_key(..., slot=True)` strips capitalised names and numbers,
so "With Kafka Streams already in place" and "With Apache Flink already in place" become one
skeleton (1,677 + 385 rows), and "(2)" vs "(3) agree the success measure" collapse too. 300
verbatim templates plus 321 slot skeletons cover 18,619 answers; the 5,335 rows with text left
cluster into 390 groups at 0.98 = 2,063 reducible (392 / 2,301 at 0.95). The 3,911 rows that
moved out of the specific set are ordinary templates whose variants had been split by step
number or capitalisation. Flag: `cluster_duplicates.py --slot-min-count 25` (0 disables, which
reproduces the old 5,074). Not yet normalised: slots inserted lowercase ("an appropriate
angular technique"), so more rows are probably templated than this count admits. The subject
slot below lowers these figures by ~8%; its numbers supersede this paragraph.

**17 Nov, subject slots:** `sentence_key(..., subject=...)` now replaces the row's own subject
with `<s>` in any casing before skeletonising, which is what the lowercase insertions needed
("an appropriate angular technique" / "an appropriate vue.js technique" become one template).
Only subjects of three characters or more are used, so "Go" and "R" never rewrite prose while
Vue, SQL and PHP still count. The effect is small and consistent, not a range collapse:

| Skeleton recurs at least | Fully templated | Specific text left | Clusters @0.98 | Reducible |
|---:|---:|---:|---:|---:|
| 10 times | 20,191 (+96) | 3,763 | 264 | **681** (-65) |
| 25 times | 18,822 (+203) | 5,132 | 364 | **1,898** (-165) |
| 50 times | 17,221 (+271) | 6,733 | 449 | **3,356** (-239) |

So the 2,063 default holds to within 8% (1,898), the range narrows from 746-3,595 to 681-3,356
(6% narrower), and the reason is that subject-slot templates were already caught by the
capitalised-name rule whenever the generator kept the subject capitalised. What remains is
free-phrase slots ("in career & behavioral" appears verbatim with the subject inserted
lowercase), which no simple normaliser catches. Conclusion: quote ~700-1,900, not a point
estimate, and stop tuning the threshold - the remaining spread is the definition, not a bug.

**17 Nov, dedupe applied + question basis:** the 29-row question-scope pass was applied, so the
dataset is **23,925 rows**; workbook rebuilt, `audit_dataset.py` PASS, `clean_answers.py` clean,
question index rebuilt (23,925 x 384) and the suite at 131 tests. The pass now has two bases.
`--basis question` (default) clusters questions at 0.98 and deletes a row only when it is within
0.98 of the row being kept: 174 chained groups cover 602 rows, 38 are protected as too far from
the kept row, leaving **390 deletable** (Education 102 -> 57, 3D Gaussian Splatting 97 -> 42,
Machine Translation 84 -> 26), cells 138/138. `--basis answer` keeps the old lens and now reports
0 further deletions at question scope, 350 within-subject, 1,869 at full scope.
The 457 question-duplicate figure was inflated by union-find chaining: the largest group put
"What was your role in TameMini-GS?" and "What are the real-world applications of TameMini-GS?"
in one 29-row cluster, worst pair 0.954. Star rule (compare to the kept row) is the fix: of the
602 rows in groups, 174 are representatives, 390 are deletable and 38 are protected as too far
from the kept row. The remaining groups are genuine paraphrase sets ("Explain the hybrid 3DGS
optimization?" x4, "Where do you see yourself in 5 years?" x4). Not applied: the 390-row
question pass is reported only, because it would delete rows whose question text differs
noticeably in small subjects (3D Gaussian Splatting 97 -> 42, Machine Translation 84 -> 26).

**17 Nov, dedupe pass:** new `dedupe_answers.py` (dry run by default) keeps one representative
per 0.98 body cluster - longest specific body, ties by dataset order - and never touches
templated rows. Building it corrected the premise behind the 1,898 figure: the duplicated bodies
sit on different questions. Of 364 clusters / 2,262 rows, answer scope would delete 1,898 rows
and remove questions along with their answers (cookies vs `entryComponents`, question cosine
0.58); subject scope deletes 379; **question scope deletes 29**, which are real paraphrases
("Tell me about Gwen?" / "Please tell me about Gwen?"). 138/138 cells stay covered, smallest
cell 1, 17 cells lose rows. Recommendation: apply the 29-row question scope, and treat the other
~1,869 as rewrite targets for `regenerate_answers.py --only-templated`. Note 29 is the overlap
of answer- and question-level duplication, not the size of either (question-only was 457 rows at
0.98). Not applied yet: the user asked to see the impact first.

**17 Nov, repair plan (not started):** the templating is concentrated - the 12 industry
subjects (Cybersecurity, AI & Data Platforms, HealthTech, Drones, E-Commerce, FinTech,
Gaming, DefenseTech, AdTech, Logistics, SaaS, FoodTech) hold ~20,300 of the ~21,800 repair
targets at ~1,700 each; the technical subjects are largely clean (Java 143, React 83, CUDA 0).
A one-subject pass on Cybersecurity is 1,711 targets = ~1,970 calls with the usual rejection
rate = **7.2 days** at 237 accepted answers/day (~1.45M tokens). Start with a 237-row pilot
(`--limit 237`) to judge quality before committing the week. New flags: `--subject NAME` and
`--only-templated`, plus a corrected free-tier estimate in `--dry-run` (it assumed output
tokens only and 300 tokens/answer, understating the cost ~3x). A 20-cluster sample at 0.98
shows the residual 'specific' text is itself slot-filled ("With <stack> already in place,
that means working with the stack we have"), so 5,074 overstates paraphrase: it is a second
template layer with the stack or deadline substituted.

**17 Nov, free measurement:** semantic clustering (local embeddings, no tokens) shows
question paraphrase duplication is small - 1,253 exact-shared rows against 241 clusters /
597 reducible rows at cosine 0.95 (179 / 457 at 0.98). Answer clustering on a 200-character
prefix originally measured the template, not duplication: only 3,399 distinct vectors existed
for 23,954 answers. Measured properly, the template is far bigger than the 799 boilerplate
rows: 300 sentences recur at least 50 times, 20,783 answers open with one of them, and
14,692 answers (61.3%) contain no unique sentence at all - 7,106 mix stock and specific
text, 2,156 are clean. Comparing only the 9,246 answers with specific text left gives 705
clusters / 5,074 reducible rows at cosine 0.98 (519 / 5,708 at 0.95, which over-merges
same-topic prose). These figures count verbatim templates only and are superseded by the
slot-aware recount below (2,063 reducible, 18,619 fully templated). Quarantine measured on seven past run files: 65 distinct questions
rejected, 9 rejected twice or more, 11 calls that would have been skipped at the default
threshold of 2 - small now, but those rows recur every time they are targeted.

**17 Nov, default-path cleanup:** the loop no longer carries the dead `--no-retry` flag or an
unused counter, the four near-identical row writes became one `record()` helper, and the
progress line prints retry counters only when `--retry` is on. A new test pins the default
path: a quota stop on the first call still writes all four in-flight rows (1 `error`, 3 `ok`).
It passes on the old chunk loop too, because that loop always drained its own chunk; it fails
if the drain is removed, which is the property it exists to hold.

**17 Nov, after the cap:** a 4-token probe returned 200 with 7,924 tokens "available", so a run
was started; its first real answer failed with `per day` and the run stopped after 4 requests
(1 ok). The cap had not reset — the probe had been served on residual budget. Resume with
`--out answers_duplicated2.csv --resume`; do not truncate that file.

**16 Nov, third attempt (valid key on disk):** 15 accepted, 1 rejected, then `429 ... tokens per
   day` — the cap itself, confirmed by the now-untruncated error text. The key was valid; the
   budget was not. Applied and audited: shared answers 1,265 → 1,250.

**Keys pasted into chat keep dying.** Both keys supplied in this conversation authenticated and then
started returning `401 Invalid API Key` after a handful of calls (one survived six answers, the next
only four). Treat a pasted key as short-lived: write the key to `.key` in a terminal instead, and if
401s start mid-run, replace the key rather than assuming a quota problem. A 401 now aborts the run
cleanly rather than failing every row.

**Second attempt the same day: 0 of 200, every call `429 tokens per day`.** That exposed a real
defect: the runner submitted every request up front, so it kept firing at a dead budget instead
of stopping. Requests are now submitted in chunks of `2 x workers` and the run aborts on the
first cap signal ("quota hit after N request(s) - M left unsent"), which is also what the
"never retry a per-day 429" rule requires in practice.

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
