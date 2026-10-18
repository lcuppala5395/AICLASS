"""Validation Tool: parse and normalise the LLM scorecard (deterministic, no LLM).

Guarantees on the returned scorecard:
  * exactly one entry for every active criterion, in criterion_id order
  * every score is a number inside [0, max_score of the criterion in SQLite]
  * every problem found is recorded as a human-readable warning
"""
from __future__ import annotations

import json
import math
import re

MAX_TEXT = 1500
MAX_RISKS = 10
NO_EVIDENCE = {"", "not provided", "n/a", "none", "not available", "not specified"}


def parse_llm_json(text: str) -> dict:
    """Extract one JSON object from raw LLM text (tolerates code fences / chatter)."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty response")
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        if start == -1:
            raise ValueError("no JSON object found") from None
        try:
            data, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed JSON ({exc.msg})") from exc
    if not isinstance(data, dict):
        raise ValueError("top-level JSON value is not an object")
    return data


def to_number(value) -> float | None:
    """Convert '7', 7, '7/10', '8 out of 10' to a float; return None if impossible."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        m = re.match(r"\s*(-?\d+(?:\.\d+)?)", value)
        if m:
            return float(m.group(1))
    return None


def _norm_compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def verify_evidence(evidence: str, doc_text: str) -> bool | None:
    """True if the quoted evidence appears in the document, False if not, None if no evidence."""
    if evidence.strip().lower() in NO_EVIDENCE:
        return None
    doc = _norm_compact(doc_text)
    fragments = [f for f in re.split(r"\.\.\.|…|\|", evidence) if len(_norm_compact(f)) >= 20]
    if not fragments:
        return _norm_compact(evidence) in doc
    return all(_norm_compact(f) in doc for f in fragments)


def _text(value, limit=MAX_TEXT) -> str:
    return (value if isinstance(value, str) else "" if value is None else str(value)).strip()[:limit]


def validate_scorecard(data: dict, criteria: list[dict], supplier_name: str, doc_text: str = ""):
    """Return (scorecard, warnings). `criteria` are the active rows from SQLite."""
    warnings: list[str] = []
    crit_by_id = {int(c["criterion_id"]): c for c in criteria}
    crit_by_name = {c["name"].strip().casefold(): int(c["criterion_id"]) for c in criteria}

    llm_name = _text(data.get("supplier_name"), 200)
    if llm_name and llm_name.casefold() != supplier_name.casefold():
        warnings.append(f"LLM returned supplier_name '{llm_name}' (expected '{supplier_name}'); expected name used.")

    raw_list = data.get("criteria")
    if not isinstance(raw_list, list):
        warnings.append("LLM output has no 'criteria' list; every criterion was set to 0.")
        raw_list = []

    entries: dict[int, dict] = {}
    for idx, entry in enumerate(raw_list):
        if not isinstance(entry, dict):
            warnings.append(f"Criterion entry #{idx + 1} is not an object and was ignored.")
            continue
        cid = to_number(entry.get("criterion_id"))
        cid = int(cid) if cid is not None and cid == int(cid) else None
        if cid not in crit_by_id:
            by_name = crit_by_name.get(_text(entry.get("name") or entry.get("criterion"), 200).casefold())
            if by_name is not None and cid is None:
                cid = by_name
        if cid not in crit_by_id:
            warnings.append(f"Entry with unknown criterion_id {entry.get('criterion_id')!r} was ignored.")
            continue
        if cid in entries:
            warnings.append(f"Duplicate result for criterion {cid}; the first one was kept.")
            continue
        entries[cid] = entry

    results = []
    for cid in sorted(crit_by_id):
        crit, entry = crit_by_id[cid], entries.get(cid)
        max_score = float(crit["max_score"])
        flags: list[str] = []
        score, valid = 0.0, False
        justification = evidence = ""
        verified = None

        if entry is None:
            flags.append("missing")
            warnings.append(f"'{crit['name']}': missing from LLM output; score set to 0 and excluded from the benchmark.")
        else:
            raw_score = to_number(entry.get("score"))
            if raw_score is None:
                flags.append("invalid_score")
                warnings.append(f"'{crit['name']}': score {entry.get('score')!r} is not numeric; set to 0 and excluded from the benchmark.")
            else:
                score, valid = raw_score, True
                if score < 0 or score > max_score:
                    clipped = min(max(score, 0.0), max_score)
                    flags.append("clipped")
                    warnings.append(f"'{crit['name']}': score {score:g} is outside 0-{max_score:g}; clipped to {clipped:g}.")
                    score = clipped
                llm_max = to_number(entry.get("max_score"))
                if llm_max is not None and llm_max != max_score:
                    warnings.append(f"'{crit['name']}': LLM used max_score {llm_max:g}; database value {max_score:g} applies.")
            justification = _text(entry.get("justification"))
            evidence = _text(entry.get("evidence"))
            if not justification:
                warnings.append(f"'{crit['name']}': no justification returned.")
            verified = verify_evidence(evidence, doc_text) if doc_text else None
            if verified is None and valid and score > 0:
                flags.append("no_evidence")
                warnings.append(f"'{crit['name']}': score {score:g} given without evidence.")
            elif verified is False:
                flags.append("evidence_not_found")
                warnings.append(f"'{crit['name']}': quoted evidence was not found in the document (possible paraphrase or hallucination).")

        results.append({
            "criterion_id": cid, "name": crit["name"], "score": round(score, 4), "max_score": max_score,
            "valid": valid, "justification": justification,
            "evidence": evidence or "Not provided", "evidence_verified": verified, "flags": flags,
        })

    risks = data.get("risks")
    if isinstance(risks, str):
        risks = [risks]
    risks = [_text(r, 300) for r in risks if _text(r, 300)][:MAX_RISKS] if isinstance(risks, list) else []

    scorecard = {"supplier_name": supplier_name, "criteria": results, "risks": risks,
                 "overall_summary": _text(data.get("overall_summary"), 800)}
    return scorecard, warnings
