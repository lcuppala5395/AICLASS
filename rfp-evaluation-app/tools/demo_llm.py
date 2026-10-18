"""Offline stand-in for an LLM - for demos, tests and running without an API key.

It is NOT an AI model. It reads the same prompt a real LLM would receive, counts
criterion-related keywords in the document, penalises vague/deferred wording, and
returns JSON in the required format with verbatim quotes as evidence.
"messy" mode additionally corrupts the output in ways real LLMs sometimes do, so
the Validation Tool can be demonstrated.
"""
from __future__ import annotations

import hashlib
import json
import re

KEYWORDS = {
    "technical capability": ["architecture", "microservice", "api", "integration", "sap", "azure ad", "saml",
                             "scal", "kubernetes", "load test", "concurrent", "connector", "event", "autoscal",
                             "multi-region", "tested", "odata", "idoc", "rest adapter", "sso"],
    "implementation plan": ["milestone", "timeline", "month", "team", "delivery manager", "risk", "buffer", "uat",
                            "pilot", "wave", "hypercare", "migration", "raci", "steering", "mitigat",
                            "contingency", "staff"],
    "commercial value": ["price", "total", "year 1", "assumption", "cap", "fixed fee", "subscription", "usd",
                         "uplift", "payment", "rate card", "taxes", "per user"],
    "security & compliance": ["iso 27001", "soc 2", "encrypt", "aes-256", "tls", "audit", "penetration", "gdpr",
                              "dpdp", "access control", "role-based", "multi-factor", "rpo", "rto", "logs",
                              "certif"],
    "support & experience": ["support", "24x7", "24x5", "response", "account manager", "reference", "contact",
                             "implementations", "customer", "uptime", "hypercare", "satisfaction",
                             "service review", "deployments"],
}
VAGUE = ["on request", "will be shared", "will be agreed", "will be defined", "to be defined", "as required",
         "as needed", "in progress", "subject to", "can be provided", "regular project meetings", "best practice",
         "industry-standard", "industry standard", "will be assigned", "further detail", "after discovery",
         "during the detailed design", "will be confirmed", "at the negotiation", "not yet", "not part of"]
RISK_MARKERS = ["no formal risk", "not yet", "in progress", "on request", "will be shared", "not included",
                "excluded", "not part of", "limited to", "will be provided at the negotiation", "will be agreed"]


def _sentences(text: str) -> list[str]:
    text = re.sub(r"--- Page \d+ ---", "\n", text)
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if len(p.strip()) > 25]


def _keywords(crit: dict) -> list[str]:
    known = KEYWORDS.get(crit["name"].strip().casefold())
    if known:
        return known
    words = re.findall(r"[a-z]{5,}", f"{crit['name']} {crit.get('description', '')}".lower())
    return sorted({w[:6] for w in words})


def _score(crit: dict, sentences: list[str]) -> dict:
    kws, hits, hit_sents, vague = _keywords(crit), set(), [], 0
    for s in sentences:
        low = s.lower()
        found = [k for k in kws if k in low]
        if found:
            hits.update(found)
            hit_sents.append((len(found), s))
            if any(v in low for v in VAGUE):
                vague += 1
    max_score = int(crit["max_score"])
    if not hits:
        score = 0
    else:
        base = min(1.0, len(hits) / max(1.0, 0.55 * len(kws))) * max_score
        score = round(max(0.0, base - 0.8 * min(vague, 3)))
    top = [s for _, s in sorted(hit_sents, key=lambda t: -t[0])[:2]]
    evidence = " ... ".join(s[:220] for s in top) if top else "Not provided"
    return {
        "criterion_id": crit["criterion_id"], "score": int(min(score, max_score)), "max_score": max_score,
        "justification": (f"[offline demo scorer] {len(hits)} relevant indicators found; "
                          f"{vague} vague or deferred statement(s)."),
        "evidence": evidence,
    }


def _pattern(name: str) -> int:
    return int(hashlib.md5(("salt-12:" + name).encode()).hexdigest(), 16) % 6


def _corrupt(data: dict, pattern: int, retry: bool) -> str:
    rows = data["criteria"]
    if pattern == 1:                                   # scores outside the allowed range
        rows[0]["score"] = rows[0]["max_score"] + 4
        rows[1]["score"] = -2
    elif pattern == 2:                                 # a criterion missing + a missing justification
        rows.pop()
        rows[0].pop("justification", None)
    elif pattern == 3:                                 # text scores, unknown criterion id
        rows[0]["score"] = f"{rows[0]['score']}/10"
        rows[1]["score"] = "n/a"
        rows.append({"criterion_id": 99, "score": 7, "max_score": 10, "justification": "?", "evidence": "?"})
    elif pattern == 4:                                 # markdown fence and chatter around the JSON
        return "Here is the evaluation:\n```json\n" + json.dumps(data) + "\n```\nHope this helps!"
    elif pattern == 5 and not retry:                   # broken JSON on first attempt, fine on retry
        return json.dumps(data)[:-40]
    return json.dumps(data)


def make_demo_llm(messy: bool = False):
    def call(system: str, user: str) -> str:
        name = re.search(r"SUPPLIER NAME:\s*(.+)", user).group(1).strip()
        criteria = json.loads(re.search(r"<criteria>\s*(.*?)\s*</criteria>", user, re.S).group(1))
        doc = re.search(r"<document>\s*(.*?)\s*</document>", user, re.S).group(1)
        sents = _sentences(doc)
        risks = [s[:200] for s in sents if any(m in s.lower() for m in RISK_MARKERS)][:3]
        rows = [_score(c, sents) for c in criteria]
        strong = sum(1 for r in rows if r["score"] >= 0.6 * r["max_score"])
        data = {"supplier_name": name, "criteria": rows, "risks": risks,
                "overall_summary": f"[offline demo scorer] {strong} of {len(rows)} criteria showed substantive evidence."}
        if messy:
            return _corrupt(data, _pattern(name), retry="PREVIOUS RESPONSE WAS INVALID" in user)
        return json.dumps(data)
    return call
