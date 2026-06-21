from __future__ import annotations

import _bootstrap  # noqa: F401

from pathlib import Path
import tempfile
import unittest

from behavior_lab.datasets.nber_best_offer.normalize import build_sample_dataset, normalize_dataset
from behavior_lab.datasets.nber_best_offer.tasks import build_tasks
from behavior_lab.offerlab_models.frontier import counteroffer_frontier


class OfferLabFrontierTests(unittest.TestCase):
    def test_frontier_is_predictive_support_bound_and_not_profit_optimization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_sample_dataset(root / "raw")
            normalize_dataset(root / "raw", root / "normalized")
            rows = build_tasks(root / "normalized")["buyer_response_to_counter"]
        context = rows[0]
        price = float(context["features"]["listing_price"])
        report = counteroffer_frontier(context, rows, [price * 0.5, price * 0.85, price * 2.0])
        self.assertFalse(report["causal_claim"])
        self.assertFalse(report["profit_optimization"])
        self.assertFalse(report["production_export_allowed"])
        self.assertNotIn("label", report["lineage"]["feature_contract"])
        unsupported = [row for row in report["frontier"] if not row["supported"]]
        supported = [row for row in report["frontier"] if row["supported"]]
        self.assertTrue(unsupported)
        self.assertTrue(supported)
        self.assertIn("nearest_comparable_counters", supported[0])
        self.assertIn("buyer_response_probabilities", supported[0])
        self.assertNotIn("expected_final_price", supported[0])
        unseen_context = dict(context)
        unseen_context["features"] = dict(context["features"])
        unseen_context["features"]["category"] = "unseen category"
        unseen = counteroffer_frontier(unseen_context, rows, [price * 0.85])
        self.assertFalse(unseen["frontier"][0]["supported"])


if __name__ == "__main__":
    unittest.main()
