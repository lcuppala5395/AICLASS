"""Tests for the Validation Tool (parsing, normalisation, warnings)."""
import unittest

from tools.validation_tool import parse_llm_json, to_number, validate_scorecard, verify_evidence

CRITERIA = [
    {"criterion_id": 1, "name": "Technical Capability", "max_score": 10},
    {"criterion_id": 2, "name": "Commercial Value", "max_score": 10},
]
DOC = "The platform uses Kubernetes with horizontal autoscaling. Three-year total price is USD 1,100,000."


def entry(cid, score, **kw):
    d = {"criterion_id": cid, "score": score, "max_score": 10, "justification": "ok",
         "evidence": "Three-year total price is USD 1,100,000."}
    d.update(kw)
    return d


class ParseTests(unittest.TestCase):
    def test_plain_fenced_and_chatty_json(self):
        self.assertEqual(parse_llm_json('{"a": 1}'), {"a": 1})
        self.assertEqual(parse_llm_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_llm_json('Sure! Here it is: {"a": 1} Hope this helps'), {"a": 1})

    def test_garbage_raises(self):
        for bad in ["", "   ", "no json here", '{"a": 1', "[1, 2]", None]:
            with self.assertRaises(ValueError):
                parse_llm_json(bad)

    def test_to_number(self):
        self.assertEqual(to_number("7/10"), 7.0)
        self.assertEqual(to_number("8.5 out of 10"), 8.5)
        self.assertEqual(to_number(6), 6.0)
        for bad in ["n/a", None, True, float("nan"), float("inf"), [], {}]:
            self.assertIsNone(to_number(bad))


class ScorecardTests(unittest.TestCase):
    def run_v(self, data):
        return validate_scorecard(data, CRITERIA, "Acme", DOC)

    def test_clean_input_has_no_warnings(self):
        sc, w = self.run_v({"supplier_name": "Acme", "criteria": [entry(1, 8, evidence="uses Kubernetes with horizontal autoscaling"), entry(2, 6)],
                            "risks": ["r1"], "overall_summary": "fine"})
        self.assertEqual(w, [])
        self.assertEqual([c["score"] for c in sc["criteria"]], [8.0, 6.0])
        self.assertTrue(all(c["valid"] for c in sc["criteria"]))

    def test_out_of_range_scores_are_clipped(self):
        sc, w = self.run_v({"criteria": [entry(1, 14), entry(2, -3)]})
        self.assertEqual([c["score"] for c in sc["criteria"]], [10.0, 0.0])
        self.assertEqual(sum("clipped" in x for x in w), 2)

    def test_missing_criterion_filled_with_zero_and_invalid(self):
        sc, w = self.run_v({"criteria": [entry(1, 8)]})
        c2 = sc["criteria"][1]
        self.assertEqual((c2["score"], c2["valid"], c2["flags"]), (0.0, False, ["missing"]))
        self.assertTrue(any("missing" in x for x in w))

    def test_non_numeric_score(self):
        sc, _ = self.run_v({"criteria": [entry(1, "n/a"), entry(2, "7/10")]})
        self.assertEqual((sc["criteria"][0]["score"], sc["criteria"][0]["valid"]), (0.0, False))
        self.assertEqual((sc["criteria"][1]["score"], sc["criteria"][1]["valid"]), (7.0, True))

    def test_unknown_and_duplicate_ids(self):
        sc, w = self.run_v({"criteria": [entry(1, 5), entry(1, 9), entry(99, 10), entry(2, 4)]})
        self.assertEqual(sc["criteria"][0]["score"], 5.0)           # first duplicate kept
        self.assertEqual(len(sc["criteria"]), 2)                    # unknown id dropped
        self.assertTrue(any("unknown criterion_id" in x for x in w))
        self.assertTrue(any("Duplicate" in x for x in w))

    def test_matches_by_name_when_id_missing(self):
        e = entry(1, 6)
        e.pop("criterion_id")
        e["name"] = "technical capability"
        sc, _ = self.run_v({"criteria": [e]})
        self.assertEqual(sc["criteria"][0]["score"], 6.0)

    def test_criteria_not_a_list(self):
        sc, w = self.run_v({"criteria": "oops"})
        self.assertEqual([c["score"] for c in sc["criteria"]], [0.0, 0.0])
        self.assertTrue(any("no 'criteria' list" in x for x in w))

    def test_database_max_score_wins(self):
        _, w = self.run_v({"criteria": [entry(1, 5, max_score=100), entry(2, 5)]})
        self.assertTrue(any("max_score 100" in x for x in w))

    def test_evidence_checks(self):
        self.assertTrue(verify_evidence("USD 1,100,000", DOC))
        self.assertTrue(verify_evidence('"Kubernetes with horizontal autoscaling" ... "price is USD 1,100,000"', DOC))
        self.assertFalse(verify_evidence("The vendor holds a Nobel prize in security.", DOC))
        self.assertIsNone(verify_evidence("Not provided", DOC))
        sc, w = self.run_v({"criteria": [entry(1, 7, evidence="Fully certified by NASA and ESA labs worldwide."), entry(2, 5, evidence="Not provided")]})
        self.assertEqual(sc["criteria"][0]["evidence_verified"], False)
        self.assertIn("evidence_not_found", sc["criteria"][0]["flags"])
        self.assertIn("no_evidence", sc["criteria"][1]["flags"])

    def test_risks_normalised(self):
        sc, _ = self.run_v({"criteria": [], "risks": "single string"})
        self.assertEqual(sc["risks"], ["single string"])
        sc, _ = self.run_v({"criteria": [], "risks": None})
        self.assertEqual(sc["risks"], [])


if __name__ == "__main__":
    unittest.main()
