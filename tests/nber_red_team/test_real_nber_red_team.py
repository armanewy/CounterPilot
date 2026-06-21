from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
import _bootstrap  # noqa: F401,E402

from behavior_lab.benchmarks.splits import (
    assert_disjoint_groups,
    chronological_group_purged_split,
    group_disjoint_split,
)
from behavior_lab.datasets.nber_best_offer.tasks import (
    assert_no_future_leakage,
    build_real_tasks_from_records,
)
from behavior_lab.offerlab_models.common import validate_feature_contract
from behavior_lab.offerlab_models.predictive import predictive_suite


class RealNberRedTeamTests(unittest.TestCase):
    def test_future_and_outcome_canary_features_are_rejected(self) -> None:
        forbidden = [
            "final_status",
            "status_id",
            "event_time",
            "response_time",
            "reference_price",
            "ref_price4",
            "final_sale_price",
            "accept_price",
            "decline_price",
            "accepted_price",
            "label",
        ]
        for name in forbidden:
            with self.subTest(name=name):
                row = {
                    "row_id": f"canary-{name}",
                    "features": {name: "leak"},
                    "observed_history": [],
                }
                self.assertFalse(assert_no_future_leakage([row]))
                self.assertFalse(validate_feature_contract([row]))

    def test_artifact_name_canary_feature_is_rejected(self) -> None:
        row = {
            "row_id": "artifact-canary",
            "features": {"artifact_name": "winner_on_hidden"},
            "observed_history": [],
        }

        self.assertFalse(assert_no_future_leakage([row]))
        self.assertFalse(validate_feature_contract([row]))

    def test_random_label_control_does_not_beat_majority_with_identical_features(self) -> None:
        rows = [
            _model_row(f"row-{idx}", "accept" if idx % 2 else "decline", f"2020-01-{idx + 1:02d}T00:00:00")
            for idx in range(12)
        ]

        report = predictive_suite("seller_next_action", rows[:8], rows[8:12], [])
        development = report["leaderboards"]["development"]
        majority = next(row for row in development if row["model_id"] == "majority")
        best = min(development, key=lambda row: row["log_loss"])

        self.assertGreaterEqual(best["log_loss"], majority["log_loss"] - 1e-9)

    def test_negative_control_references_are_executable_metadata(self) -> None:
        rows = [
            _model_row(f"row-{idx}", "accept" if idx % 2 else "decline", f"2020-02-{idx + 1:02d}T00:00:00")
            for idx in range(12)
        ]

        report = predictive_suite("seller_next_action", rows[:8], rows[8:12], [])
        first = report["leaderboards"]["development"][0]

        self.assertIn("seller_next_action:random_label_permutation", first["negative_control_references"])
        self.assertIn("seller_next_action:random_row_split", first["negative_control_references"])
        self.assertIn("seller_next_action:same_timestamp_ordering", first["negative_control_references"])
        self.assertIn("seller_next_action:artifact_name_canary", first["negative_control_references"])

    def test_final_status_in_observed_history_is_rejected(self) -> None:
        row = {
            "row_id": "history-canary",
            "features": {"category": "parts"},
            "observed_history": [{"turn_index": 1, "status": "accepted"}],
        }

        self.assertFalse(assert_no_future_leakage([row]))

    def test_chronological_split_purges_boundary_crossing_listings(self) -> None:
        rows = [
            {"row_id": "a", "timestamp": "2020-01-01T00:00:00", "listing_id": "cross"},
            {"row_id": "b", "timestamp": "2020-01-02T00:00:00", "listing_id": "train-only"},
            {"row_id": "c", "timestamp": "2020-01-03T00:00:00", "listing_id": "train-2"},
            {"row_id": "d", "timestamp": "2020-01-04T00:00:00", "listing_id": "cross"},
            {"row_id": "e", "timestamp": "2020-01-05T00:00:00", "listing_id": "hidden-2"},
        ]

        split = chronological_group_purged_split(rows, time_key="timestamp", group_key="listing_id")

        self.assertIn("cross", split.purged_group_ids)
        self.assertEqual(split.purged_rows, 2)
        self.assertTrue(assert_disjoint_groups(split, group_key="listing_id"))

    def test_seller_disjoint_split_blocks_seller_identifier_memorization(self) -> None:
        rows = [
            {"row_id": f"row-{idx}", "seller_id": f"seller-{idx // 2}", "label": str(idx % 2)}
            for idx in range(12)
        ]

        split = group_disjoint_split(rows, group_key="seller_id")

        self.assertTrue(assert_disjoint_groups(split, group_key="seller_id"))

    def test_censored_rows_are_not_converted_to_rejection(self) -> None:
        listing = {
            "listing_id": "listing-1",
            "seller_id": "seller-1",
            "category": "parts",
            "condition": "used",
            "listing_price": 100.0,
            "reference_price": None,
            "final_sale_price": None,
            "sold_by_best_offer": False,
        }
        censored_turn = {
            "thread_id": "thread-1",
            "listing_id": "listing-1",
            "buyer_id": "buyer-1",
            "seller_id": "seller-1",
            "turn_index": 1,
            "actor": "buyer",
            "action": "offer",
            "amount": 70.0,
            "status_id": 8,
            "status": "declined_other_buyer_accepted",
            "event_time": "2020-01-01T00:00:00",
            "response_time": "2020-01-01T01:00:00",
        }

        tasks = build_real_tasks_from_records([listing], [censored_turn])

        self.assertEqual(tasks["seller_next_action"], [])
        self.assertEqual(tasks["agreement"], [])


def _model_row(row_id: str, label: str, timestamp: str) -> dict[str, object]:
    return {
        "row_id": row_id,
        "listing_id": f"listing-{row_id}",
        "seller_id": f"seller-{row_id}",
        "timestamp": timestamp,
        "label": label,
        "features": {
            "category": "parts",
            "condition": "used",
            "listing_price": 100.0,
            "current_actor": "buyer",
            "current_action": "offer",
            "current_amount": 70.0,
            "offer_to_asking_ratio": 0.70,
            "round_number": 1,
            "prior_turn_count": 0,
            "prior_counter_count": 0,
        },
        "observed_history": [],
    }


if __name__ == "__main__":
    unittest.main()
