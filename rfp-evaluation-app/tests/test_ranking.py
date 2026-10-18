"""Tests for the deterministic Ranking Tool (formulas, benchmarks, PPI, tie-breaks)."""
import random
import unittest

from ranking import rank_suppliers

CRITERIA = [
    {"criterion_id": 1, "name": "A", "weight": 60, "max_score": 10},
    {"criterion_id": 2, "name": "B", "weight": 40, "max_score": 10},
]


def card(name, s1, s2, date="2026-10-01", exp=3.0, valid=(True, True)):
    return {"supplier_name": name, "submission_date": date, "experience_rating": exp,
            "criteria": [{"criterion_id": 1, "score": s1, "valid": valid[0]},
                         {"criterion_id": 2, "score": s2, "valid": valid[1]}]}


def by_name(result):
    return {s["supplier_name"]: s for s in result["suppliers"]}


class FormulaTests(unittest.TestCase):
    def test_absolute_benchmark_gap_relative_ppi(self):
        r = by_name(rank_suppliers([card("X", 8, 5), card("Y", 6, 10)], CRITERIA))
        self.assertEqual(r["X"]["absolute_score"], 68.0)      # 8/10*60 + 5/10*40
        self.assertEqual(r["Y"]["absolute_score"], 76.0)      # 6/10*60 + 10/10*40
        x1, x2 = r["X"]["criteria"]
        self.assertEqual((x1["benchmark"], x1["gap"], x1["relative_pct"]), (8.0, 0.0, 100.0))
        self.assertEqual((x2["benchmark"], x2["gap"], x2["relative_pct"]), (10.0, -5.0, 50.0))
        self.assertEqual(r["X"]["ppi"], 80.0)                 # (100*60 + 50*40) / 100
        self.assertEqual(r["Y"]["ppi"], 85.0)                 # (75*60 + 100*40) / 100

    def test_zero_benchmark_is_safe(self):
        res = rank_suppliers([card("X", 0, 5), card("Y", 0, 10)], CRITERIA)
        for s in res["suppliers"]:
            self.assertEqual(s["criteria"][0]["relative_pct"], 100.0)   # nobody scored: neutral
            self.assertEqual(s["criteria"][0]["gap"], 0.0)
        self.assertTrue(any("no supplier earned a score above 0" in w for w in res["warnings"]))

    def test_invalid_scores_never_become_benchmark(self):
        # X's 10 on criterion 2 is a validator fill-in (invalid); the benchmark must be Y's 4.
        res = by_name(rank_suppliers([card("X", 5, 10, valid=(True, False)), card("Y", 5, 4)], CRITERIA))
        self.assertEqual(res["Y"]["criteria"][1]["benchmark"], 4.0)

    def test_weights_not_100_are_rescaled(self):
        crit = [dict(c, weight=c["weight"] / 2) for c in CRITERIA]      # totals 50
        res = rank_suppliers([card("X", 10, 10)], crit)
        self.assertTrue(res["weights_rescaled"])
        self.assertEqual(res["suppliers"][0]["absolute_score"], 100.0)

    def test_zero_total_weight_raises(self):
        with self.assertRaises(ValueError):
            rank_suppliers([card("X", 5, 5)], [dict(c, weight=0) for c in CRITERIA])

    def test_inputs_not_mutated(self):
        cards = [card("X", 8, 5), card("Y", 6, 10)]
        before = repr(cards)
        rank_suppliers(cards, CRITERIA)
        self.assertEqual(repr(cards), before)


class TieBreakTests(unittest.TestCase):
    def order(self, cards):
        return [s["supplier_name"] for s in rank_suppliers(cards, CRITERIA)["suppliers"]]

    def test_ppi_beats_everything(self):
        cards = [card("Early", 5, 5, "2026-01-01", 5), card("Better", 9, 9, "2026-12-31", 1)]
        self.assertEqual(self.order(cards), ["Better", "Early"])

    def test_earlier_date_breaks_ppi_tie(self):
        cards = [card("Late", 5, 5, "2026-10-09", 5), card("Early", 5, 5, "2026-10-01", 1)]
        self.assertEqual(self.order(cards), ["Early", "Late"])

    def test_higher_experience_breaks_date_tie(self):
        cards = [card("Low", 5, 5, "2026-10-01", 2.0), card("High", 5, 5, "2026-10-01", 4.5)]
        self.assertEqual(self.order(cards), ["High", "Low"])

    def test_name_ascending_is_last_resort(self):
        cards = [card("Zeta", 5, 5), card("alpha", 5, 5), card("Beta", 5, 5)]
        self.assertEqual(self.order(cards), ["alpha", "Beta", "Zeta"])

    def test_ranks_are_sequential_and_explained(self):
        res = rank_suppliers([card("Zeta", 5, 5), card("Alpha", 5, 5), card("Mid", 7, 7)], CRITERIA)
        self.assertEqual([s["rank"] for s in res["suppliers"]], [1, 2, 3])
        self.assertEqual([s["supplier_name"] for s in res["suppliers"]], ["Mid", "Alpha", "Zeta"])
        self.assertIn("name in ascending order", " ".join(res["tie_break_explanation"]))

    def test_same_inputs_same_order_regardless_of_input_order(self):
        base = [card(n, s, s, d, e) for n, s, d, e in [
            ("A", 7, "2026-10-02", 3.0), ("B", 7, "2026-10-02", 3.0), ("C", 7, "2026-10-01", 1.0),
            ("D", 9, "2026-10-05", 2.0), ("E", 7, "2026-10-02", 4.0)]]
        expected = None
        for seed in range(20):
            shuffled = base[:]
            random.Random(seed).shuffle(shuffled)
            names = [s["supplier_name"] for s in rank_suppliers(shuffled, CRITERIA)["suppliers"]]
            expected = expected or names
            self.assertEqual(names, expected)
        self.assertEqual(expected, ["D", "C", "E", "A", "B"])


if __name__ == "__main__":
    unittest.main()
