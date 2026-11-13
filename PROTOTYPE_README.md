# Semantic Question Search — Prototype

Local prototype that lets you find interview questions **by meaning**, filtered by
subject and difficulty, and returns the reference answer for each hit.

```
combined_dataset.csv ──► validation ──► bge-small embeddings (ONNX, CPU) ──► cached index
                                                                              │
                    query ──► embedding ──► cosine ranking ──► filters ──► top-k + answers
```

## What it covers

| Requirement | Where it lives |
|---|---|
| 1. Load and validate the dataset | `load_dataset()` / `--validate` — checks the five required columns, empty fields, duplicate question texts and invalid difficulty values, then prints a summary |
| 2. Embed every question | `Embedder` + `build_index()` — `BAAI/bge-small-en-v1.5` (384-d, ONNX Runtime on CPU), cached to `embeddings/questions_*.npy` |
| 3. Search by meaning | `search()` — cosine similarity over normalised embeddings; the query carries bge's retrieval instruction prefix |
| 4. Filter by subject and difficulty | `--subject` (exact, case-insensitive) and `--difficulty` (exact, case-insensitive) |
| 5. Top 5 with reference answers | `-k 5` default; output shows rank, cosine score, subject, difficulty, question and the full stored answer |

## Setup

```bash
python -m pip install -r requirements.txt
```

The model files are already in `embeddings/model/` (66 MB, `bge-small-en-v1.5`, quantised ONNX).
If they are missing, download them with:

```bash
python -m pip install fastembed
python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"
```

then copy `model_optimized.onnx` (rename to `model.onnx`), `tokenizer.json`, `config.json`,
`tokenizer_config.json` and `special_tokens_map.json` from the fastembed cache into `embeddings/model/`.

## Usage

```bash
# validate only
python search_app.py --validate

# semantic query, top 5
python search_app.py -q "how do I stop a component from re-rendering too often"

# with filters
python search_app.py -q "memory corruption under load" -s C++ -d Hard -k 5

# interactive loop
python search_app.py --interactive
```

First run builds the index (about 10 minutes on this machine, ~43 questions/s);
later runs load the cached `.npy` in about a second. `--rebuild` forces a rebuild.
The cache is keyed on a SHA-1 of the CSV plus the row count, so editing the dataset
triggers a rebuild automatically on the next run.

## Example

```
Query: 'why does my vue component re-render so much'   [subject~'Vue.js']
1. [0.712]  Vue.js  ·  Hard
   Q: A Vue 3 component re-renders far too often on a large reactive list...
   A: I start by measuring rather than guessing, using the Vue devtools...
```

## Notes and limits

- Embeddings are computed on `subject + question`; the answer text is **not** embedded,
  so retrieval scores reflect question meaning only.
- `--subject` is an **exact** match (case-insensitive, surrounding whitespace ignored), so
  `-s Java` returns only Java and never `JavaScript`. An unknown subject exits with an error
  listing close matches, so `-s jav` suggests `Java, JavaScript`.
- Only the retained answer per question is stored (see `DATA_LIMITATIONS.md`).
- Score is cosine similarity in [0, 1]; above ~0.65 usually means a genuinely close
  paraphrase, 0.45–0.65 topical, below ~0.45 is weak.
- CPU only. A GPU or larger batch size would cut the index build from minutes to seconds.
