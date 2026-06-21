from __future__ import annotations

import _bootstrap  # noqa: F401

import unittest

from behavior_lab.benchmarks.metrics import classification_accuracy, multiclass_log_loss, regression_rmse
from behavior_lab.benchmarks.splits import assert_disjoint_groups, chronological_split, group_disjoint_split


class BenchmarkFoundationTests(unittest.TestCase):
    def test_chronological_split_orders_by_time(self) -> None:
        rows = [{"id": "c", "time": "2026-01-03"}, {"id": "a", "time": "2026-01-01"}, {"id": "b", "time": "2026-01-02"}]
        split = chronological_split(rows, time_key="time")
        self.assertEqual(split.train[0]["id"], "a")
        self.assertEqual(split.hidden[-1]["id"], "c")

    def test_group_disjoint_split_keeps_sellers_apart(self) -> None:
        rows = [{"row": index, "seller": f"s{index}"} for index in range(6)]
        split = group_disjoint_split(rows, group_key="seller")
        self.assertTrue(assert_disjoint_groups(split, group_key="seller"))

    def test_metrics_handle_classification_and_regression(self) -> None:
        predictions = [
            {"label": "a", "prediction": "a", "probabilities": {"a": 0.8, "b": 0.2}},
            {"label": "b", "prediction": "a", "probabilities": {"a": 0.7, "b": 0.3}},
        ]
        self.assertEqual(classification_accuracy(predictions), 0.5)
        self.assertGreater(multiclass_log_loss(predictions), 0.0)
        self.assertEqual(regression_rmse([{"label": 2.0, "prediction": 2.0}]), 0.0)


if __name__ == "__main__":
    unittest.main()
