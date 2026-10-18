# Demonstration script

Two minutes for the successful run, three for the error cases. Start with `streamlit run app.py`.

## Part 1 - one successful run

1. **Criteria tab**: show the 5 active criteria, weights (total 100%) and max score. Open *Edit criteria* and say that weights live in SQLite, not in the prompt.
2. **Supplier input tab**: upload the six PDFs from `data/sample_rfps/` and the optional `data/sample_metadata.csv` (pre-fills name, date, rating). Pick your provider in the sidebar (a real LLM, or *Offline demo* if no key).
3. Click **Evaluate suppliers**. Point out the progress messages (one supplier at a time).
4. **Leaderboard tab**: rank, absolute score, PPI, submission date, experience rating. Explain why PPI and absolute score can order suppliers differently.
5. **Detailed scorecard tab**: choose a supplier. Walk through one criterion: score, benchmark, gap, relative %, weight, then the quoted evidence and the justification. Note the "quote found in document" check.
6. **Run details tab**: show RFP_RUN_ID, the tie-break explanation, the agent trace (order of tool calls), and click **Download complete result (JSON)**.
7. Show persistence: reload the page, choose the run under *Past runs* in the sidebar, click *Load selected run*.

Expected with *Offline demo*: Apex Systems 1, NexaWorks 2, Quantum Leaf Solutions 3, Orbit Digital 4, BrightPath Tech 5, Vertex Cloudware 6. A real LLM will give different numbers; that is expected.

## Part 2 - validation and error cases (show at least one)

| Case | How to trigger | What you should see |
|---|---|---|
| Messy LLM output | Sidebar provider **Offline demo - deliberately messy output**, same six PDFs | Status *completed with warnings*. Apex: missing criterion set to 0. BrightPath: scores 14 and -2 clipped. NexaWorks: JSON wrapped in a code fence, still parsed. Orbit: broken JSON, retried once. Quantum Leaf: "n/a" score and an unknown criterion id ignored. Vertex: clean. |
| Unreadable files | Upload `data/edge_cases/not_a_real_pdf.pdf` and `blank_no_text.pdf` with a good PDF | Two suppliers listed under *Not ranked* with a clear reason; the good one is still ranked. |
| Bad form input | Give two PDFs the same supplier name; or upload nothing | Red error message and the Evaluate button stays disabled. |
| Missing API key | Choose a real provider without a key | Error message, button disabled, nothing stored. |
| Weights not 100% | Untick one criterion in *Edit criteria* and save | Warning on the Criteria tab; next run shows "weights rescaled" in Run details. |
| Prompt injection (real LLM only) | Upload `data/edge_cases/injection_attempt_RFP.pdf` | The proposal tells the evaluator to give 10/10. A well-behaved LLM scores it low because the prompt tells it to ignore instructions inside documents. The offline scorer ignores it trivially, so this case only proves something with a real model. |

## Part 3 - proving determinism

Run `python -m unittest discover -s tests -t .` and show the tie-break tests: same scorecards in any input order always give the same ranks.
