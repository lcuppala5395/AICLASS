"""Ranking Tool: deterministic scoring, peer benchmarking, PPI and tie-breaks.

Pure Python. No LLM, no randomness, no I/O. Given the same validated scorecards
and criteria it always returns the same numbers and the same order.

Formulas (weights are normalised so the active weights total 100):
  absolute_score  = SUM( score_i / max_score_i * weight_i )                  (0-100)
  benchmark_i     = highest VALID score for criterion i across all suppliers
  gap_i           = score_i - benchmark_i                                    (<= 0)
  relative_pct_i  = score_i / benchmark_i * 100       (100 when benchmark_i = 0)
  PPI             = SUM( relative_pct_i * weight_i ) / SUM( weight_i )

A score filled in by the validator (missing / non-numeric LLM result) counts as 0
for the supplier but is NOT "valid", so it can never become a benchmark.
"""
from __future__ import annotations

import copy
from datetime import date

TIE_BREAK_RULE = (
    "1) Higher PPI  ->  2) Earlier submission date  ->  "
    "3) Higher historical experience rating  ->  4) Supplier name A-Z"
)
DECIMALS = 4  # PPI and scores are rounded to 4 decimals BEFORE comparing, so float noise cannot change a rank

_RULE_LABELS = {"ppi": "PPI", "submission_date": "Submission date",
                "experience_rating": "Experience rating", "supplier_name": "Supplier name"}


def _to_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def normalise_weights(criteria: list[dict]):
    """Return (criteria with 'weight_normalised', was_rescaled)."""
    total = sum(float(c["weight"]) for c in criteria)
    if total <= 0:
        raise ValueError("Active criteria weights add up to zero; nothing to score.")
    out = []
    for c in sorted(criteria, key=lambda c: int(c["criterion_id"])):
        d = dict(c)
        d["criterion_id"] = int(c["criterion_id"])
        d["weight_normalised"] = float(c["weight"]) / total * 100.0
        out.append(d)
    return out, abs(total - 100.0) > 1e-6


def _sort_key(card: dict):
    return (-card["ppi"], _to_date(card["submission_date"]), -float(card["experience_rating"]),
            card["supplier_name"].casefold(), card["supplier_name"])


def _decisive_rule(above: dict, below: dict) -> str:
    if above["ppi"] != below["ppi"]:
        return "ppi"
    if _to_date(above["submission_date"]) != _to_date(below["submission_date"]):
        return "submission_date"
    if float(above["experience_rating"]) != float(below["experience_rating"]):
        return "experience_rating"
    return "supplier_name"


def _explain(above: dict, below: dict, rule: str) -> str | None:
    a, b = above["supplier_name"], below["supplier_name"]
    if rule == "submission_date":
        return (f"{a} ranks above {b}: PPI tied at {above['ppi']:.{DECIMALS}f}; earlier submission date "
                f"({above['submission_date']} vs {below['submission_date']}).")
    if rule == "experience_rating":
        return (f"{a} ranks above {b}: PPI and submission date tied; higher experience rating "
                f"({float(above['experience_rating']):g} vs {float(below['experience_rating']):g}).")
    if rule == "supplier_name":
        return (f"{a} ranks above {b}: PPI, submission date and experience rating all tied; "
                f"name in ascending order ('{a}' before '{b}').")
    return None


def rank_suppliers(scorecards: list[dict], criteria: list[dict]) -> dict:
    """Score, benchmark and rank validated scorecards. Inputs are not modified."""
    if not criteria:
        raise ValueError("No criteria supplied.")
    crit, rescaled = normalise_weights(criteria)
    cards = copy.deepcopy(scorecards)
    warnings: list[str] = []
    if rescaled:
        total = sum(float(c["weight"]) for c in criteria)
        warnings.append(f"Active weights total {total:g}%, not 100%; they were rescaled proportionally to 100% for scoring.")

    # -- 1. per-supplier criterion rows + absolute weighted score
    for card in cards:
        by_id = {int(e["criterion_id"]): e for e in card.get("criteria", [])}
        rows, absolute = [], 0.0
        for c in crit:
            cid = c["criterion_id"]
            entry = dict(by_id.get(cid) or {"criterion_id": cid, "justification": "", "evidence": "Not provided",
                                           "evidence_verified": None, "flags": ["missing"]})
            if cid not in by_id:
                warnings.append(f"{card['supplier_name']}: no scorecard entry for '{c['name']}'; scored 0.")
            score = min(max(float(entry.get("score", 0.0) or 0.0), 0.0), float(c["max_score"]))
            entry.update(criterion_id=cid, name=c["name"], weight=float(c["weight"]),
                         weight_normalised=c["weight_normalised"], max_score=float(c["max_score"]),
                         score=score, valid=bool(entry.get("valid", cid in by_id)))
            entry["points"] = score / entry["max_score"] * c["weight_normalised"]
            absolute += entry["points"]
            rows.append(entry)
        card["criteria"], card["_absolute"] = rows, absolute

    # -- 2. benchmark per criterion = best VALID score among all suppliers
    benchmarks = {}
    for c in crit:
        valid_scores = [e["score"] for card in cards for e in card["criteria"]
                        if e["criterion_id"] == c["criterion_id"] and e["valid"]]
        benchmarks[c["criterion_id"]] = max(valid_scores) if valid_scores else 0.0
        if not valid_scores or benchmarks[c["criterion_id"]] == 0:
            warnings.append(f"'{c['name']}': no supplier earned a score above 0, so relative performance is "
                            "set to 100% for everyone on this criterion (it does not separate suppliers).")

    # -- 3. gap, relative %, PPI
    for card in cards:
        ppi_sum = 0.0
        for e in card["criteria"]:
            bench = benchmarks[e["criterion_id"]]
            e["benchmark"] = round(bench, DECIMALS)
            e["gap"] = round(e["score"] - bench, DECIMALS)
            rel = 100.0 if bench == 0 else e["score"] / bench * 100.0
            e["relative_pct"] = round(rel, DECIMALS)
            ppi_sum += rel * e["weight_normalised"]
            e["points"] = round(e["points"], DECIMALS)
        card["absolute_score"] = round(card.pop("_absolute"), DECIMALS)
        card["ppi"] = round(ppi_sum / 100.0, DECIMALS)   # weights sum to 100

    # -- 4. deterministic order, sequential ranks
    cards.sort(key=_sort_key)
    explanation = []
    for i, card in enumerate(cards):
        card["rank"] = i + 1
        if i == 0:
            card["decided_by"] = "-"
            continue
        rule = _decisive_rule(cards[i - 1], card)
        card["decided_by"] = _RULE_LABELS[rule]
        line = _explain(cards[i - 1], card, rule)
        if line:
            explanation.append(line)
    if not explanation:
        explanation = ["No tie-breaks were needed: every supplier has a distinct PPI."]

    leaderboard = [{"rank": c["rank"], "supplier_name": c["supplier_name"],
                    "absolute_score": c["absolute_score"], "ppi": c["ppi"],
                    "submission_date": str(c["submission_date"]),
                    "experience_rating": float(c["experience_rating"]),
                    "decided_by": c["decided_by"]} for c in cards]
    return {
        "weights_rescaled": rescaled,
        "benchmarks": {str(k): round(v, DECIMALS) for k, v in benchmarks.items()},
        "suppliers": cards,
        "leaderboard": leaderboard,
        "tie_break_rule": TIE_BREAK_RULE,
        "tie_break_explanation": explanation,
        "warnings": warnings,
    }
