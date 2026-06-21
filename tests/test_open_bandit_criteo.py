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


if __name__ == "__main__":
    unittest.main()
