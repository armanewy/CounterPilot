from __future__ import annotations

import _bootstrap  # noqa: F401

import unittest

from behavior_lab.datasets.auctionnet.strategy import compare_strategies


class AuctionNetAdapterTests(unittest.TestCase):
    def test_strategy_report_is_simulation_only(self) -> None:
        report = compare_strategies(budget=20.0)
        self.assertEqual(report["source_id"], "auctionnet")
        self.assertEqual(report["evidence_role"], "SIMULATION")
        self.assertTrue(report["simulation_only"])
        self.assertIn("Do not use", report["warning"])
        self.assertEqual({item["strategy"] for item in report["reports"]}, {"fixed_policy", "conservative_policy", "learned_policy", "over_aggressive_policy"})
        for item in report["reports"]:
            self.assertIn("regret", item)
            self.assertIn("reward_variance", item)


if __name__ == "__main__":
    unittest.main()
