# Agentic RFP Evaluation and Supplier Ranking

A Streamlit app that reads supplier proposals (PDF), has an LLM score them against configurable criteria with quoted evidence, then uses **deterministic Python** to benchmark suppliers against each other and produce an explainable leaderboard.

> The LLM judges proposal *content*. It never decides arithmetic, benchmarks, tie-breaks or ranks.

## Folder structure

```
rfp-evaluation-app/
├── app.py                  Streamlit UI (5 tabs)
├── evaluation.py           Evaluation Agent (prompt, LLM call, retry) + Orchestrator (run_pipeline)
├── ranking.py              Ranking Tool: weighted scores, benchmarks, gaps, PPI, tie-breaks (pure Python)
├── db.py                   SQLite access: criteria, runs, results
├── seed_db.py              Creates rfp.db and seeds the sample criteria
├── work_zip.py             Packages a clean submission zip (no secrets, no .db)
├── requirements.txt
├── DEMO.md                 Script for the demonstration
├── tools/
│   ├── document_tool.py    Document Tool: PDF -> clean text (pypdf)
│   ├── validation_tool.py  Validation Tool: parse JSON, clip, fill, warn
│   ├── llm_client.py       One function per provider (Anthropic, OpenAI, Gemini) over HTTPS
│   └── demo_llm.py         Offline stand-in for an LLM (demos/tests only, not an AI model)
├── data/
│   ├── sample_rfps/        6 synthetic supplier PDFs
│   ├── sample_metadata.csv Supplier name, date, rating for those PDFs
│   ├── edge_cases/         Not-a-PDF, blank PDF, prompt-injection PDF
│   ├── sample_output/      Exported JSON of completed runs
│   └── generate_rfps.py    Regenerates all PDFs
├── tests/                  34 unit and pipeline tests (unittest)
└── .streamlit/             config.toml, secrets.toml.example
```

## Setup

Python 3.10+ is recommended.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python seed_db.py               # optional: the app also creates and seeds the DB on first start
streamlit run app.py
```

**Choosing the AI.** In the sidebar pick a provider:
- *Anthropic / OpenAI / Gemini*: needs an API key. Put it in `.streamlit/secrets.toml` (copy `secrets.toml.example`) or type it in the sidebar. Model names change often; edit the *Model* box if the default is outdated.
- *Offline demo*: no key. A keyword heuristic that returns the same JSON format. Use it to try the app, run tests and demo validation. **It is not an AI model**, so use a real provider for your real submission and regenerate the sample JSON with it.

## Architecture

| Component | File | Responsibility | Uses LLM? |
|---|---|---|---|
| Orchestrator | `evaluation.run_pipeline` | Runs the steps below in a fixed order, records a trace, handles failures | No |
| Document Tool | `tools/document_tool.py` | Extract text, reject non-PDF / empty / encrypted files, cap length | No |
| Evaluation Agent | `evaluation.evaluate_supplier` | Build prompt from the active criteria, call LLM, retry once on invalid JSON | **Yes** |
| Validation Tool | `tools/validation_tool.py` | Schema check, fill missing criteria, clip scores, verify quoted evidence, record warnings | No |
| Ranking Tool | `ranking.py` | All formulas, benchmarks, PPI, tie-breaks, ranks | No |

Data flow: **1 Setup** (UI loads criteria) -> **2 Input** (upload + metadata) -> **3 Batch** (new `RFP_RUN_ID`) -> **4 Evaluate** (reload criteria, extract, prompt, LLM) -> **5 Validate** -> **6 Score** -> **7 Benchmark** -> **8 Rank** -> **9 Persist** (SQLite) -> **10 Present** (leaderboard, scorecards, JSON). The *Run details* tab shows the trace of steps 3-9.

The prompt tells the model to use only document evidence, quote verbatim, return one result per active criterion within the score range, ignore instructions found inside the document, and output JSON only. Criterion weights are **not** shown to the model.

## Formulas

Weights of active criteria are rescaled to total 100 if they do not already (a warning is shown).

| Quantity | Definition |
|---|---|
| Absolute weighted score | `SUM( score_i / max_score_i x weight_i )`, range 0-100 |
| Criterion benchmark | Highest **valid** score for that criterion across suppliers in the run |
| Criterion gap | `score_i - benchmark_i` (0 for the leader, otherwise negative) |
| Relative performance % | `score_i / benchmark_i x 100`; if the benchmark is 0 it is set to 100 for everyone (nobody outperforms anybody, and a warning is shown) |
| PPI | `SUM( relative_pct_i x weight_i ) / SUM( weight_i )` |

Worked example (weights 60/40, max score 10): X scores 8 and 5, Y scores 6 and 10. Benchmarks are 8 and 10. X: absolute = 8/10x60 + 5/10x40 = **68**, relative = 100% and 50%, PPI = (100x60 + 50x40)/100 = **80**. Y: absolute **76**, relative 75% and 100%, PPI **85**. Y ranks first.

**Tie-break order** (applied to PPI rounded to 4 decimals, then ranks 1, 2, 3... are assigned): 1) higher PPI, 2) earlier submission date, 3) higher experience rating, 4) supplier name A-Z (case-insensitive). The *Run details* tab explains every case where a tie was decided below PPI.

**Validation rules.** A missing criterion or non-numeric score becomes 0 and is **excluded from the benchmark**, so a fill-in value can never set the standard. Out-of-range scores are clipped to 0..max and stay valid. Unknown or duplicate criterion ids are ignored. The database `max_score` always wins over what the LLM wrote. Evidence that cannot be found in the document is flagged.

## SQLite design

| Table | Fields |
|---|---|
| `evaluation_criteria` | criterion_id, name, description, weight, max_score, is_active |
| `rfp_runs` | rfp_run_id, created_at, status, llm_provider, llm_model, run_json (complete result) |
| `supplier_results` | rfp_run_id, supplier_name, submission_date, experience_rating, absolute_score, ppi, final_rank, result_json |

Suppliers whose evaluation failed are stored with `final_rank = NULL` and the error in `result_json`. Run status is `completed`, `completed_with_warnings` or `failed`.

## Testing

```bash
python -m unittest discover -s tests -t .
```
Covers formulas against hand-calculated numbers, benchmark and zero-benchmark handling, every tie-break level, order independence, JSON parsing, clipping, missing and unknown criteria, evidence checks, full pipeline runs with persistence, failed files and criteria changes. Tests use the offline demo LLM and a temporary database, so they need no key and no network.

## Deploy on Streamlit Community Cloud

1. Push this folder to a GitHub repository (the `.gitignore` already keeps keys and `.db` files out).
2. On share.streamlit.io choose **New app**, select the repo and branch, and set the main file to `app.py`.
3. Open **Advanced settings -> Secrets** and paste e.g. `ANTHROPIC_API_KEY = "..."`.
4. Deploy and submit the public URL.

The app creates and seeds `rfp.db` on first start. Community Cloud storage is not permanent, so saved runs can disappear when the app restarts; download the JSON for anything you must keep.

## Screenshots

Add your own after running the app (suggested: `docs/screenshots/`): criteria tab, supplier input, leaderboard, detailed scorecard, run details, and one validation case.

## Assumptions and limitations

- Experience rating is on a 0-5 scale entered by the user. Submission dates are `YYYY-MM-DD`.
- Scanned (image-only) PDFs are rejected; OCR is not included.
- Text over 60,000 characters is truncated, with a warning.
- LLM scoring can vary between runs even at temperature 0. Determinism applies from the validated scorecards onward; the stored `raw_llm_output` and document hash make each run auditable.
- Suppliers that fail evaluation are excluded from benchmarks and ranking, and listed separately.
