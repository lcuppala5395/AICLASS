"""SQLite persistence: criteria, runs and per-supplier results.

The database file is created automatically on first use. Set RFP_DB_PATH to
move it. Every function accepts an optional db_path so tests can use a
temporary database.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("RFP_DB_PATH") or Path(__file__).resolve().parent / "rfp.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS evaluation_criteria (
    criterion_id INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    description  TEXT NOT NULL,
    weight       REAL NOT NULL CHECK (weight >= 0),
    max_score    INTEGER NOT NULL DEFAULT 10 CHECK (max_score > 0),
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS rfp_runs (
    rfp_run_id   TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    status       TEXT NOT NULL,
    llm_provider TEXT,
    llm_model    TEXT,
    run_json     TEXT
);

CREATE TABLE IF NOT EXISTS supplier_results (
    rfp_run_id        TEXT NOT NULL REFERENCES rfp_runs(rfp_run_id),
    supplier_name     TEXT NOT NULL,
    submission_date   TEXT NOT NULL,
    experience_rating REAL NOT NULL,
    absolute_score    REAL,
    ppi               REAL,
    final_rank        INTEGER,
    result_json       TEXT NOT NULL,
    PRIMARY KEY (rfp_run_id, supplier_name)
);
"""

# (criterion_id, name, description, weight, max_score, is_active)
DEFAULT_CRITERIA = [
    (1, "Technical Capability", "Architecture, integrations, scalability, technical fit", 30, 10, 1),
    (2, "Implementation Plan", "Timeline, milestones, staffing, risk plan", 20, 10, 1),
    (3, "Commercial Value", "Pricing clarity, total cost, assumptions", 20, 10, 1),
    (4, "Security & Compliance", "Controls, certifications, privacy, auditability", 20, 10, 1),
    (5, "Support & Experience", "Support model, similar projects, references", 10, 10, 1),
]

_RUN_EXTRA_COLUMNS = {"llm_provider": "TEXT", "llm_model": "TEXT", "run_json": "TEXT"}


@contextmanager
def connect(db_path=None):
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def init_db(db_path=None, seed: bool = True) -> None:
    """Create tables (and seed default criteria if the criteria table is empty)."""
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(rfp_runs)")}
        for col, col_type in _RUN_EXTRA_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE rfp_runs ADD COLUMN {col} {col_type}")
        if seed and conn.execute("SELECT COUNT(*) FROM evaluation_criteria").fetchone()[0] == 0:
            conn.executemany("INSERT INTO evaluation_criteria VALUES (?,?,?,?,?,?)", DEFAULT_CRITERIA)


# --------------------------------------------------------------------------- criteria
def get_all_criteria(db_path=None) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM evaluation_criteria ORDER BY criterion_id").fetchall()
    return [dict(r) for r in rows]


def get_active_criteria(db_path=None) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM evaluation_criteria WHERE is_active = 1 ORDER BY criterion_id"
        ).fetchall()
    return [dict(r) for r in rows]


def weights_total(criteria: list[dict]) -> float:
    return round(sum(float(c["weight"]) for c in criteria), 6)


def update_criteria(rows: list[dict], db_path=None) -> None:
    """Update weight, max_score and is_active for existing criteria.

    rows: dicts with criterion_id, weight, max_score, is_active.
    Raises ValueError (and changes nothing) if any value is invalid.
    """
    cleaned = []
    for r in rows:
        try:
            weight = float(r["weight"])
            max_score = int(r["max_score"])
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError(f"Invalid weight/max score for criterion {r.get('criterion_id')}") from exc
        if weight < 0 or weight != weight:
            raise ValueError(f"Weight for criterion {r['criterion_id']} must be 0 or more.")
        if max_score <= 0:
            raise ValueError(f"Max score for criterion {r['criterion_id']} must be above 0.")
        cleaned.append((weight, max_score, 1 if r["is_active"] else 0, int(r["criterion_id"])))
    with connect(db_path) as conn:
        conn.executemany(
            "UPDATE evaluation_criteria SET weight=?, max_score=?, is_active=? WHERE criterion_id=?",
            cleaned,
        )


# --------------------------------------------------------------------------- runs
def new_run_id() -> str:
    now = datetime.now(timezone.utc)
    return f"RFP-{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4].upper()}"


def create_run(run_id, created_at, status, provider, model, db_path=None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO rfp_runs (rfp_run_id, created_at, status, llm_provider, llm_model) VALUES (?,?,?,?,?)",
            (run_id, created_at, status, provider, model),
        )


def set_run_status(run_id, status, db_path=None) -> None:
    with connect(db_path) as conn:
        conn.execute("UPDATE rfp_runs SET status=? WHERE rfp_run_id=?", (status, run_id))


def save_run_results(run_id: str, run_result: dict, db_path=None) -> None:
    """Persist the complete result of one run under a single rfp_run_id (atomic)."""
    with connect(db_path) as conn:
        conn.execute("DELETE FROM supplier_results WHERE rfp_run_id=?", (run_id,))
        for s in run_result.get("suppliers", []):
            conn.execute(
                "INSERT INTO supplier_results VALUES (?,?,?,?,?,?,?,?)",
                (run_id, s["supplier_name"], s["submission_date"], float(s["experience_rating"]),
                 s["absolute_score"], s["ppi"], s["rank"], json.dumps(s, ensure_ascii=False)),
            )
        for f in run_result.get("failed_suppliers", []):
            conn.execute(
                "INSERT INTO supplier_results VALUES (?,?,?,?,?,?,?,?)",
                (run_id, f["supplier_name"], f["submission_date"], float(f["experience_rating"]),
                 None, None, None, json.dumps(f, ensure_ascii=False)),
            )
        conn.execute(
            "UPDATE rfp_runs SET status=?, run_json=? WHERE rfp_run_id=?",
            (run_result["status"], json.dumps(run_result, ensure_ascii=False), run_id),
        )


def list_runs(limit: int = 25, db_path=None) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT rfp_run_id, created_at, status, llm_provider, llm_model "
            "FROM rfp_runs ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def load_run(run_id: str, db_path=None) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT run_json FROM rfp_runs WHERE rfp_run_id=?", (run_id,)).fetchone()
    if not row or not row["run_json"]:
        return None
    return json.loads(row["run_json"])
