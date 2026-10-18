"""Streamlit UI for the Agentic RFP Evaluation and Supplier Ranking mini project.

Screens (tabs): Criteria | Supplier input | Leaderboard | Detailed scorecard | Run details
Run:  streamlit run app.py
"""
from __future__ import annotations

import io
import json
import os
import re
from datetime import date

import pandas as pd
import streamlit as st

import db
import evaluation
from tools.llm_client import DEFAULT_MODELS, ENV_KEYS, PROVIDERS, LLMConfigError, needs_key

MAX_FILE_MB = 10
MAX_SUPPLIERS = 12   # simple cost guard for a classroom deployment

st.set_page_config(page_title="Agentic RFP Evaluation", page_icon="📄", layout="wide")
db.init_db()   # creates rfp.db and seeds the sample criteria on first start (idempotent)


# --------------------------------------------------------------------------- helpers
def secret(name: str) -> str | None:
    """Read a key from Streamlit secrets or the environment (never from the database)."""
    try:
        value = st.secrets.get(name)
    except Exception:           # no secrets.toml present
        value = None
    return (str(value or os.environ.get(name) or "").strip()) or None


def md_safe(text: str) -> str:
    """Escape Markdown/LaTeX characters so LLM or document text is shown literally."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!$|<>~])", r"\\\1", str(text))


def default_supplier_name(filename: str) -> str:
    stem = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
    stem = re.sub(r"^\d+[_\-\s]*", "", stem)
    stem = re.sub(r"[_\-\s]*(RFP|proposal|response)$", "", stem, flags=re.IGNORECASE)
    return stem.replace("_", " ").strip() or "Supplier"


def parse_metadata_csv(upload):
    """Optional CSV to pre-fill the form. Returns (mapping by filename, signature, warnings)."""
    if upload is None:
        return {}, "nocsv", []
    sig = f"csv{upload.size}"
    try:
        df = pd.read_csv(io.BytesIO(upload.getvalue()))
        df.columns = [str(c).strip().lower() for c in df.columns]
        missing = {"file", "supplier_name", "submission_date", "experience_rating"} - set(df.columns)
        if missing:
            return {}, sig, [f"Metadata CSV is missing column(s): {', '.join(sorted(missing))}."]
        mapping = {}
        for r in df.to_dict("records"):
            mapping[str(r["file"]).strip()] = {
                "supplier_name": str(r["supplier_name"]).strip(),
                "submission_date": date.fromisoformat(str(r["submission_date"]).strip()[:10]),
                "experience_rating": min(max(float(r["experience_rating"]), 0.0), 5.0),
            }
        return mapping, sig, []
    except Exception as exc:
        return {}, sig, [f"Could not read the metadata CSV ({exc}). Fill the form manually."]


# --------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("AI settings")
    configured = [p for p in ENV_KEYS if secret(ENV_KEYS[p])]
    provider_keys = list(PROVIDERS)
    default_provider = configured[0] if configured else "offline-demo"
    provider = st.selectbox("Provider", provider_keys, index=provider_keys.index(default_provider),
                            format_func=lambda k: PROVIDERS[k])
    model = st.text_input("Model", value=DEFAULT_MODELS[provider], key=f"model_{provider}")
    api_key = None
    if needs_key(provider):
        api_key = secret(ENV_KEYS[provider])
        if api_key:
            st.caption(f"Using {ENV_KEYS[provider]} from secrets/environment.")
        else:
            api_key = st.text_input(ENV_KEYS[provider], type="password",
                                    help="Held in memory for this session only; never stored.") or None
    else:
        st.info("No API key needed. The offline scorer is a keyword heuristic for demos and testing, "
                "not a real AI model.")

    st.divider()
    st.header("Past runs")
    past = db.list_runs()
    if past:
        labels = {r["rfp_run_id"]: f"{r['rfp_run_id']} ({r['status']})" for r in past}
        chosen = st.selectbox("Saved runs", list(labels), format_func=lambda k: labels[k])
        if st.button("Load selected run"):
            loaded = db.load_run(chosen)
            if loaded:
                st.session_state["run"] = loaded
            else:
                st.error("That run has no stored result.")
    else:
        st.caption("No saved runs yet.")

st.title("Agentic RFP Evaluation and Supplier Ranking")
st.caption("The AI reads and judges each proposal. Python does the arithmetic, benchmarking, tie-breaks and ranking.")

tab_criteria, tab_input, tab_board, tab_detail, tab_run = st.tabs(
    ["1. Criteria", "2. Supplier input", "3. Leaderboard", "4. Detailed scorecard", "5. Run details"])

# --------------------------------------------------------------------------- 1. criteria
with tab_criteria:
    st.subheader("Active evaluation criteria")
    active = db.get_active_criteria()
    total_weight = db.weights_total(active)
    if not active:
        st.error("No active criteria. Activate at least one criterion below.")
    else:
        table = pd.DataFrame(active)[["criterion_id", "name", "description", "weight", "max_score"]]
        table.columns = ["ID", "Criterion", "What the AI inspects", "Weight (%)", "Max score"]
        st.dataframe(table, hide_index=True)
        c1, c2 = st.columns(2)
        c1.metric("Total weight of active criteria", f"{total_weight:g}%")
        c2.metric("Active criteria", len(active))
        if abs(total_weight - 100) > 0.01:
            st.warning(f"Weights total {total_weight:g}%, not 100%. They will be rescaled proportionally to 100% when scoring.")

    with st.expander("Edit criteria (instructor / admin)"):
        st.caption("Changes are saved to SQLite and used by the next evaluation. No code or prompt change is needed.")
        edit_df = pd.DataFrame(db.get_all_criteria())
        edit_df["is_active"] = edit_df["is_active"].astype(bool)
        edited = st.data_editor(
            edit_df, key="criteria_editor", hide_index=True, num_rows="fixed",
            disabled=["criterion_id", "name", "description"],
            column_config={
                "weight": st.column_config.NumberColumn("Weight (%)", min_value=0.0, max_value=100.0, step=1.0),
                "max_score": st.column_config.NumberColumn("Max score", min_value=1, max_value=100, step=1),
                "is_active": st.column_config.CheckboxColumn("Active"),
            })
        if st.button("Save criteria changes"):
            try:
                db.update_criteria(edited.to_dict("records"))
                st.success("Criteria saved.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

# --------------------------------------------------------------------------- 2. supplier input
with tab_input:
    st.subheader("Upload supplier proposals")
    files = st.file_uploader("Supplier RFP responses (PDF, several allowed)", type=["pdf"], accept_multiple_files=True)
    meta_upload = st.file_uploader("Optional: metadata CSV to pre-fill the form "
                                   "(columns: file, supplier_name, submission_date, experience_rating)", type=["csv"])
    meta, meta_sig, meta_warnings = parse_metadata_csv(meta_upload)
    for w in meta_warnings:
        st.warning(w)

    submissions, errors, notes, seen = [], [], [], set()
    for i, f in enumerate(files or []):
        uid = f"{getattr(f, 'file_id', None) or f'{i}-{f.name}-{f.size}'}-{meta_sig}"
        m = meta.get(f.name, {})
        with st.container(border=True):
            st.markdown(f"**{f.name}** · {f.size / 1024:.0f} KB")
            c1, c2, c3 = st.columns([3, 2, 2])
            name = c1.text_input("Supplier name", value=m.get("supplier_name") or default_supplier_name(f.name), key=f"n_{uid}")
            sdate = c2.date_input("Submission date", value=m.get("submission_date") or date.today(), key=f"d_{uid}")
            rating = c3.number_input("Historical experience rating (0-5)", min_value=0.0, max_value=5.0,
                                     value=float(m.get("experience_rating", 3.0)), step=0.1, key=f"r_{uid}")
        name = name.strip()
        if not name:
            errors.append(f"{f.name}: supplier name is required.")
        elif len(name) > 80:
            errors.append(f"{f.name}: supplier name is longer than 80 characters.")
        elif name.casefold() in seen:
            errors.append(f"Supplier name '{name}' is used more than once; names must be unique.")
        seen.add(name.casefold())
        if f.size > MAX_FILE_MB * 1024 * 1024:
            errors.append(f"{f.name}: file is larger than {MAX_FILE_MB} MB.")
        if sdate > date.today():
            notes.append(f"{name or f.name}: submission date is in the future.")
        submissions.append({"name": name, "filename": f.name, "pdf_bytes": f.getvalue(),
                            "submission_date": sdate.isoformat(), "experience_rating": float(rating)})

    if not files:
        errors.append("Upload at least one supplier PDF.")
    if len(submissions) > MAX_SUPPLIERS:
        errors.append(f"At most {MAX_SUPPLIERS} suppliers can be evaluated per batch.")
    if not db.get_active_criteria():
        errors.append("There are no active evaluation criteria (see the Criteria tab).")
    if needs_key(provider) and not api_key:
        errors.append(f"No API key for {PROVIDERS[provider]}. Add it in the sidebar or in secrets.")
    for msg in errors:
        st.error(msg)
    for msg in notes:
        st.warning(msg)

    if st.button("Evaluate suppliers", type="primary", disabled=bool(errors)):
        bar = st.progress(0.0, text="Starting...")
        try:
            result = evaluation.run_pipeline(
                submissions, provider, model, api_key,
                progress=lambda frac, msg: bar.progress(min(max(frac, 0.0), 1.0), text=msg))
        except (LLMConfigError, evaluation.PipelineError) as exc:
            bar.empty()
            st.error(str(exc))
        except Exception as exc:    # unexpected bug: show it instead of a blank page
            bar.empty()
            st.error(f"Unexpected error: {exc.__class__.__name__}: {exc}")
        else:
            st.session_state["run"] = result
            bar.empty()
            if result["status"] == "failed":
                st.error(f"Run {result['rfp_run_id']} failed: no supplier could be evaluated. See Run details.")
            else:
                st.success(f"Run {result['rfp_run_id']} finished ({result['status'].replace('_', ' ')}). "
                           "Open the Leaderboard tab.")

run = st.session_state.get("run")
EMPTY = "No results yet. Evaluate suppliers in tab 2, or load a saved run from the sidebar."

# --------------------------------------------------------------------------- 3. leaderboard
with tab_board:
    if not run:
        st.info(EMPTY)
    else:
        st.subheader(f"Leaderboard · {run['rfp_run_id']}")
        if run["leaderboard"]:
            board = pd.DataFrame(run["leaderboard"])[
                ["rank", "supplier_name", "absolute_score", "ppi", "submission_date", "experience_rating", "decided_by"]]
            board.columns = ["Rank", "Supplier", "Absolute score (/100)", "PPI", "Submission date",
                             "Experience rating", "Placed below previous by"]
            st.dataframe(board, hide_index=True)
            st.caption("Absolute score = weighted sum of score/max per criterion. PPI (Peer Performance Index) = weighted "
                       "average of each criterion score relative to the best peer (100 = best on every criterion).")
            st.bar_chart(board.set_index("Supplier")[["Absolute score (/100)", "PPI"]])
            st.markdown("**Tie-break order:** " + run["tie_break_rule"])
        else:
            st.error("No supplier could be evaluated in this run.")
        if run["failed_suppliers"]:
            st.markdown("**Not ranked (evaluation failed)**")
            st.dataframe(pd.DataFrame(run["failed_suppliers"])[["supplier_name", "filename", "error"]], hide_index=True)

# --------------------------------------------------------------------------- 4. detailed scorecard
with tab_detail:
    if not run or not run["suppliers"]:
        st.info(EMPTY)
    else:
        names = [s["supplier_name"] for s in run["suppliers"]]
        pick = st.selectbox("Supplier", names)
        s = next(x for x in run["suppliers"] if x["supplier_name"] == pick)
        m1, m2, m3 = st.columns(3)
        m1.metric("Rank", f"#{s['rank']} of {len(names)}")
        m2.metric("Absolute score", f"{s['absolute_score']:.2f} / 100")
        m3.metric("PPI", f"{s['ppi']:.2f}")

        rows = [{"Criterion": e["name"], "Weight (%)": e["weight_normalised"], "Score": e["score"],
                 "Max": e["max_score"], "Benchmark": e["benchmark"], "Gap": e["gap"],
                 "Relative %": e["relative_pct"], "Weighted points": e["points"]} for e in s["criteria"]]
        st.dataframe(pd.DataFrame(rows), hide_index=True)

        st.markdown("**Evidence and justification**")
        for e in s["criteria"]:
            with st.expander(f"{e['name']} · {e['score']:g}/{e['max_score']:g}"):
                st.markdown("**Justification:** " + (md_safe(e["justification"]) or "_none returned_"))
                st.markdown("**Evidence (quote from the proposal):** " + md_safe(e["evidence"]))
                if e["evidence_verified"] is True:
                    st.caption("✔ Quote found in the document text.")
                elif e["evidence_verified"] is False:
                    st.warning("Quote NOT found in the document text. Treat this score with caution.")
                if e["flags"]:
                    st.caption("Validation flags: " + ", ".join(e["flags"]))
        if s["risks"]:
            st.markdown("**Risks identified**")
            for r in s["risks"]:
                st.markdown("- " + md_safe(r))
        if s["overall_summary"]:
            st.markdown("**Summary:** " + md_safe(s["overall_summary"]))
        if s["warnings"]:
            with st.expander(f"Validation warnings ({len(s['warnings'])})"):
                for w in s["warnings"]:
                    st.markdown("- " + md_safe(w))
        d = s["document"]
        st.caption(f"Source: {d['filename']} · {d['pages']} page(s) · {d['characters']:,} characters · "
                   f"sha256 {d['sha256'][:12]}…" + (" · TEXT TRUNCATED" if d["truncated"] else ""))

# --------------------------------------------------------------------------- 5. run details
with tab_run:
    if not run:
        st.info(EMPTY)
    else:
        st.subheader("Run details")
        a, b, c = st.columns(3)
        a.markdown(f"**RFP_RUN_ID**\n\n`{run['rfp_run_id']}`")
        b.markdown(f"**Status**\n\n{run['status'].replace('_', ' ')}")
        c.markdown(f"**LLM**\n\n`{run['llm']['provider']}` / `{run['llm']['model']}`")
        st.caption(f"Created {run['created_at']} · prompt version {run.get('prompt_version', '?')}")

        with st.expander(f"Warnings ({len(run['warnings'])})", expanded=bool(run["warnings"])):
            if run["warnings"]:
                for w in run["warnings"]:
                    st.markdown("- " + md_safe(w))
            else:
                st.write("No warnings.")

        st.markdown("**Tie-break rule:** " + run["tie_break_rule"])
        for line in run["tie_break_explanation"]:
            st.markdown("- " + md_safe(line))

        st.markdown("**Criteria and weights used in this run**")
        cdf = pd.DataFrame(run["criteria"])[["criterion_id", "name", "weight", "weight_normalised", "max_score"]]
        cdf.columns = ["ID", "Criterion", "Weight (stored)", "Weight (used, total 100)", "Max score"]
        st.dataframe(cdf, hide_index=True)

        st.markdown("**Agent trace (order of tool calls)**")
        st.dataframe(pd.DataFrame(run["trace"])[["time", "step", "supplier", "message"]], hide_index=True)

        st.download_button("Download complete result (JSON)", data=json.dumps(run, indent=2, ensure_ascii=False),
                           file_name=f"{run['rfp_run_id']}.json", mime="application/json")
