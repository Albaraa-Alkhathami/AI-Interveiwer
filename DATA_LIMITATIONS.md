# Final Dataset — Limitations

**File:** `combined_dataset.csv`
**Snapshot:** 23,850 rows, 46 subjects, 3 difficulty levels, 133/138 subject×difficulty combinations covered.
**Schemas:** `prompt, question, answer, subject, difficulty, data_flag`
**Prepared:** 13 November 2026. All figures below were measured on this exact file.

Use this document whenever the dataset is used for training, evaluation, reporting or comparison. Several limitations are structural and cannot be fixed by sampling differently.

---

## 1. Composition

| Property | Value |
|---|---:|
| Rows | 23,850 |
| Distinct question texts | 23,850 (0 exact duplicates) |
| Subjects | 46 |
| Easy / Medium / Hard | 8,642 / 8,091 / 7,117 |
| Subject × difficulty combinations | 133 of 138 |
| Average answer length | 1,326 characters (min 30, max 2,268) |
| Rows flagged `limited data (<20 questions)` | 72 |

## 2. Provenance and lineage

1. The source combined file held **28,770 rows**.
2. **309 rows** were exact duplicates — every row from `qa_sheet.xlsx` already existed verbatim in the source, so the merge contributed nothing and those rows were dropped.
3. **4,931 rows** were repeat copies of the same question with a differently generated answer. One row per question was kept, preferring a non-empty answer that ends in proper punctuation, then the longest.
4. **11 rows** were authored to close coverage gaps (see §6).

Consequences of step 3 are covered in §4 and §5 and are the most important limitation in this document.

## 3. Class imbalance — subjects

- The twelve industry subjects (Cybersecurity, AI & Data Platforms, Drones & Aerial Platforms, E-Commerce & Global Trade, HealthTech & BioTech, FinTech, Gaming, AdTech, DefenseTech, SaaS & Cloud Platforms, Logistics & Supply Chain, FoodTech) hold **85.8%** of all rows.
- The remaining 34 subjects share **14.2%**, and 10 of them have fewer than 30 questions.
- Six subjects have fewer than 20 questions and are marked `limited data (<20 questions)` in the `data_flag` column:

| Subject | Questions |
|---|---:|
| C++ | 15 |
| English Language | 14 |
| Software Testing | 14 |
| CUDA | 13 |
| Version Control | 12 |
| General Computer Science | 4 |

**Impact:** any per-subject statistic, model fine-tune or benchmark slice on these subjects is unreliable — 4 to 15 examples cannot represent a topic. Aggregate metrics are dominated by the twelve large subjects.

**Mitigation:** report per-subject metrics with sample sizes; exclude or pool the six flagged subjects; never compare subject accuracy without weighting.

## 4. Lost answer variants

The deduplication kept **one answer per question**. The removed copies were not duplicates of each other: the same question appeared up to 35 times, each with a differently worded answer.

**Impact:**
- The dataset is **not** a source of multiple acceptable answers per question. If you need answer diversity for preference ranking, evaluation rubrics or contrastive training, this file cannot provide it.
- The surviving answer is "most complete by length and punctuation", which is a crude proxy for best. It is not a quality judgement.
- Any question that would be answered differently in different contexts (e.g. by seniority) now carries only one viewpoint.

**Mitigation:** the pre-clean backup `combined_dataset_original_backup.csv` (28,770 rows) still holds every variant; use it if answer diversity matters.

## 5. Subject labels are single-label and partly arbitrary

When the same question existed under several subjects, only the retained row's subject survived.

**Impact:**
- 117 question texts originally spanned more than one subject; each now appears under exactly one.
- Some rows are labelled with a subject that was one of several valid contexts, so the subject column is **not** a multi-label topic map and is mildly noisy.
- Coverage figures per subject therefore understate true topical reach.

**Mitigation:** treat `subject` as a primary label, not an exhaustive classification. Re-label from question text if multi-label use is required.

## 6. Eleven rows have different provenance

The 11 rows added to close gaps (Personal Information ×2, Leadership & Teamwork ×2, Vue.js, LLM Fine-Tuning, DevOps, C++, Software Testing, CUDA, Version Control) were **written by an assistant**, not produced by the original generation pipeline.

**Impact:** wording, length and structure follow the dataset's style but are not statistically comparable to the rest. They are a small fraction (0.05%) but they sit in exactly the sparse subjects where they matter most.

**Mitigation:** filter them out by question text if strict provenance is required; they are the only rows added after the CSV snapshot of 13 Nov 2026.

## 7. Difficulty labels are unvalidated

Difficulty is inherited from the source and was not re-checked against content.

- Hard is the smallest class (7,117 vs 8,091 Medium), and **3 subjects have no Hard questions at all**: Education, Career Goals & Motivation and English Language.
- Labels appear to track question type rather than measured complexity (Easy ≈ definitional, Medium ≈ comparison, Hard ≈ design/debug/scenario).

**Impact:** difficulty-balanced sampling is impossible for the 7 subjects missing a level, and "Hard" is not calibrated across subjects.

**Mitigation:** do not use difficulty as a difficulty score; treat it as a coarse category. Re-annotate with a rubric if it drives training or evaluation.

## 8. Missing coverage — the 5 remaining gaps

| Subject | Missing level |
|---|---|
| English Language | Medium, Hard |
| Education | Hard |
| Machine Translation (Gwen) | Easy |
| Career Goals & Motivation | Hard |

**Impact:** those subjects cannot be used for level-stratified comparison, and English Language has only Easy questions.

## 9. Answer quality is unverified

The answers are machine-generated and were not fact-checked.

- No ground-truth answer, rubric or human rating exists anywhere in the file.
- 0.7% of answers open with filler ("Certainly.", "Of course!", "Sure.") — cosmetic but a detectable generation artefact.
- 3 answers do not end with terminal punctuation; the shortest is 30 characters, so a handful of answers are too thin to be useful.
- Answers are long (average 1,326 characters) and first-person, containing **invented personal and project details** (e.g. named publications, employer specifics). These are plausible-sounding but fabricated and must not be presented as real facts.

**Impact:** the file is suitable as a question bank and as style-consistent generation data; it is **not** suitable as a factual reference, a source of ground truth, or evidence of anyone's real experience.

## 10. Duplicate removal is textual only

Questions were compared after lowercasing, whitespace collapsing and trailing-punctuation stripping.

**Impact:** paraphrases of the same question ("What is X?" vs "Can you explain X?") remain as separate rows. 0 exact duplicates exist, but semantic duplication persists and is not measured here.

**Mitigation:** run embedding-based clustering before treating rows as independent samples.

## 11. Prompt column is templated, not independent

- 23,850 rows carry 21,084 distinct prompts.
- Nearly all follow a few templates: `Prepare me for an interview on {subject}`, `You are a senior professional in the {subject} domain…`, `Interview questions about {subject}`.
- The prompt is therefore almost a function of the subject and is not an independent experimental variable.

**Impact:** prompt-level analysis, prompt-diversity claims or prompt ablation on this column are not meaningful.

## 12. Uneven granularity

Industry-subject answers are frequently long multi-paragraph scenarios (1,500–2,000 characters); small-subject answers are short definitional replies (150–300 characters).

**Impact:** length-based statistics, truncation thresholds and token budgets are dominated by subject, not by question difficulty. Any model trained here will associate subject with answer length.

## 13. Missing metadata

The file carries no information about:

- source documents, licences or consent for the underlying material;
- generation model, prompt version, temperature or date per row;
- language (assumed English throughout);
- author/reviewer, or any human sign-off;
- timestamps, so rows cannot be ordered or traced.

**Impact:** the dataset cannot be audited or safely redistributed without additional provenance work.

## 14. Intended use

**Reasonable:** a question bank for interview practice, a seed set for synthetic data generation, retrieval corpora for a study tool, and exploratory analysis of coverage.

**Not appropriate:** factual question answering, measuring real candidate ability, benchmarking without per-subject sample sizes, or any claim that the answers reflect verified expertise.

---

### Reporting checklist

When quoting this dataset, state at minimum: row count (23,850), that answers are machine-generated and unverified, that one answer per question is retained, that 12 subjects hold 85.8% of rows, and that 6 subjects carry the `limited data` flag.

*Lineage: 28,770 source rows → −309 exact duplicates → 23,839 unique questions → +11 authored rows = 23,850. Original preserved at `combined_dataset_original_backup.csv`.*
