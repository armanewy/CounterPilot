from __future__ import annotations

import _bootstrap  # noqa: F401

import unittest

from behavior_lab.datasets.criteo_uplift.uplift import simple_uplift_report
from behavior_lab.datasets.open_bandit.ope import evaluate_policy


class WiderNetBenchmarkTests(unittest.TestCase):
    def test_open_bandit_ope_reports_support_and_estimators(self) -> None:
        logs = [
            {"action": "a", "propensity": 0.5, "reward": 1.0},
            {"action": "b", "propensity": 0.5, "reward": 0.0},
            {"action": "a", "propensity": 0.5, "reward": 1.0},
            {"action": "b", "propensity": 0.5, "reward": 1.0},
        ]
        report = evaluate_policy(logs, lambda _row: {"a": 1.0, "b": 0.0})
        self.assertEqual(report["source_id"], "open_bandit_dataset")
        self.assertEqual(len(report["estimates"]), 4)
        self.assertGreater(report["estimates"][1]["effective_sample_size"], 0)
        snips = next(item for item in report["estimates"] if item["estimator"] == "self_normalized_ips")
        self.assertLessEqual(snips["confidence_interval"][0], snips["value"])
        self.assertGreaterEqual(snips["confidence_interval"][1], snips["value"])

    def test_open_bandit_zero_target_probability_is_not_support_violation(self) -> None:
        logs = [{"action": "a", "propensity": 1.0, "reward": 1.0}]
        report = evaluate_policy(logs, lambda _row: {"a": 0.0})
        ips = next(item for item in report["estimates"] if item["estimator"] == "ips")
        self.assertEqual(ips["support_violations"], 0)

    def test_criteo_uplift_report_is_research_only(self) -> None:
        rows = [
            {"treatment": 0, "conversion": 0},
            {"treatment": 0, "conversion": 0},
            {"treatment": 1, "conversion": 1},
            {"treatment": 1, "conversion": 0},
        ]
        report = simple_uplift_report(rows)
        self.assertFalse(report["production_export_allowed"])
        self.assertEqual(report["average_treatment_effect"], 0.5)
        self.assertEqual(report["negative_control_ate"], 0.0)
        self.assertTrue(report["negative_control_passed"])
        self.assertEqual(report["negative_control_method"], "exact_permutation_null")
        self.assertIn("not evidence", report["negative_control_interpretation"])
        self.assertEqual(report["permutation_samples"], 6)
        self.assertIn("confidence_interval", report)
        self.assertEqual(report["treatment_count"], 2)


if __name__ == "__main__":
    unittest.main()
