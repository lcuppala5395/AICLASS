"""End-to-end tests of the orchestrator with the offline demo LLM and a temporary SQLite file."""
import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import db
import evaluation
from tools.llm_client import LLMConfigError, get_llm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load_samples():
    subs = []
    with open(DATA / "sample_metadata.csv", newline="") as fh:
        for row in csv.DictReader(fh):
            subs.append({"name": row["supplier_name"], "filename": row["file"],
                         "pdf_bytes": (DATA / "sample_rfps" / row["file"]).read_bytes(),
                         "submission_date": row["submission_date"], "experience_rating": float(row["experience_rating"])})
    return subs


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        db.init_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_run_is_persisted_and_reloadable(self):
        res = evaluation.run_pipeline(load_samples(), "offline-demo", db_path=self.db)
        self.assertEqual(res["status"], "completed")
        self.assertEqual([r["rank"] for r in res["leaderboard"]], list(range(1, 7)))
        con = sqlite3.connect(self.db)
        rows = con.execute("SELECT supplier_name, final_rank, result_json FROM supplier_results WHERE rfp_run_id=?",
                           (res["rfp_run_id"],)).fetchall()
        self.assertEqual(len(rows), 6)
        json.loads(rows[0][2])                                         # result_json is valid JSON
        self.assertEqual(db.load_run(res["rfp_run_id"], self.db), res)  # reload == fresh result

    def test_same_validated_scorecards_give_same_ranking(self):
        a = evaluation.run_pipeline(load_samples(), "offline-demo", db_path=self.db)
        b = evaluation.run_pipeline(list(reversed(load_samples())), "offline-demo", db_path=self.db)
        strip = lambda r: [(x["rank"], x["supplier_name"], x["absolute_score"], x["ppi"]) for x in r["leaderboard"]]
        self.assertEqual(strip(a), strip(b))

    def test_messy_llm_output_is_normalised(self):
        res = evaluation.run_pipeline(load_samples(), "offline-demo-messy", db_path=self.db)
        self.assertEqual(res["status"], "completed_with_warnings")
        self.assertEqual(len(res["leaderboard"]), 6)
        text = " ".join(res["warnings"])
        for expected in ["clipped", "missing from LLM output", "not numeric", "unknown criterion_id", "retried once"]:
            self.assertIn(expected, text)
        for s in res["suppliers"]:
            for c in s["criteria"]:
                self.assertTrue(0 <= c["score"] <= c["max_score"])

    def test_bad_files_are_reported_not_crashing(self):
        subs = load_samples()[:1]
        for name, f in [("Fake", "not_a_real_pdf.pdf"), ("Blank", "blank_no_text.pdf")]:
            subs.append({"name": name, "filename": f, "pdf_bytes": (DATA / "edge_cases" / f).read_bytes(),
                         "submission_date": "2026-10-01", "experience_rating": 3})
        res = evaluation.run_pipeline(subs, "offline-demo", db_path=self.db)
        self.assertEqual(res["status"], "completed_with_warnings")
        self.assertEqual({f["supplier_name"] for f in res["failed_suppliers"]}, {"Fake", "Blank"})
        self.assertEqual(len(res["leaderboard"]), 1)
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM supplier_results WHERE final_rank IS NULL").fetchone()[0], 2)

    def test_all_failed_run_status(self):
        bad = [{"name": "Fake", "filename": "x.pdf", "pdf_bytes": b"hello", "submission_date": "2026-10-01",
                "experience_rating": 1}]
        res = evaluation.run_pipeline(bad, "offline-demo", db_path=self.db)
        self.assertEqual((res["status"], res["leaderboard"]), ("failed", []))

    def test_latest_active_criteria_are_used(self):
        rows = db.get_all_criteria(self.db)
        for r in rows:
            r["is_active"] = r["criterion_id"] in (1, 3)
        db.update_criteria(rows, self.db)
        res = evaluation.run_pipeline(load_samples()[:2], "offline-demo", db_path=self.db)
        self.assertEqual([c["criterion_id"] for c in res["criteria"]], [1, 3])
        self.assertTrue(res["weights_rescaled"])                       # 30 + 20 = 50 -> rescaled to 100
        self.assertTrue(any("rescaled" in w for w in res["warnings"]))

    def test_no_active_criteria_blocks_the_run(self):
        rows = db.get_all_criteria(self.db)
        for r in rows:
            r["is_active"] = False
        db.update_criteria(rows, self.db)
        with self.assertRaises(evaluation.PipelineError):
            evaluation.run_pipeline(load_samples()[:1], "offline-demo", db_path=self.db)
        self.assertEqual(db.list_runs(db_path=self.db), [])            # nothing stored

    def test_missing_api_key_raises_before_storing(self):
        with self.assertRaises(LLMConfigError):
            get_llm("anthropic", api_key="")
        with self.assertRaises(LLMConfigError):
            evaluation.run_pipeline(load_samples()[:1], "openai", api_key="", db_path=self.db)
        self.assertEqual(db.list_runs(db_path=self.db), [])

    def test_criteria_update_validation(self):
        with self.assertRaises(ValueError):
            db.update_criteria([{"criterion_id": 1, "weight": -5, "max_score": 10, "is_active": True}], self.db)
        with self.assertRaises(ValueError):
            db.update_criteria([{"criterion_id": 1, "weight": 10, "max_score": 0, "is_active": True}], self.db)


if __name__ == "__main__":
    unittest.main()
