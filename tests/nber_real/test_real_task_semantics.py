from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
import _bootstrap  # noqa: F401,E402

from behavior_lab.datasets.nber_best_offer.tasks import (
    assert_no_future_leakage,
    build_real_tasks_from_records,
)


def listing(**overrides):
    payload = {
        "listing_id": "listing-1",
        "seller_id": "seller-1",
        "category": "parts",
        "condition": "used",
        "listing_price": 100.0,
        "reference_price": None,
        "reference_price_unavailable_reason": "excluded",
        "final_sale_price": 82.0,
        "sold_by_best_offer": True,
    }
    payload.update(overrides)
    return payload


def turn(thread_id: str, turn_index: int, *, actor: str, action: str, status_id: int, amount: float, **overrides):
    payload = {
        "thread_id": thread_id,
        "listing_id": "listing-1",
        "buyer_id": f"buyer-{thread_id}",
        "seller_id": "seller-1",
        "turn_index": turn_index,
        "actor": actor,
        "action": action,
        "amount": amount,
        "status_id": status_id,
        "status": str(status_id),
        "event_time": f"2020-01-0{turn_index}T00:00:00",
        "response_time": f"2020-01-0{turn_index}T12:00:00",
    }
    payload.update(overrides)
    return payload


class RealTaskSemanticsTests(unittest.TestCase):
    def test_status_families_map_to_seller_and_buyer_labels(self) -> None:
        rows = [
            turn("accept", 1, actor="buyer", action="offer", status_id=1, amount=70.0),
            turn("auto-accept", 1, actor="buyer", action="offer", status_id=9, amount=71.0),
            turn("decline", 1, actor="buyer", action="offer", status_id=2, amount=72.0),
            turn("expire", 1, actor="buyer", action="offer", status_id=0, amount=73.0),
            turn("counter", 1, actor="buyer", action="offer", status_id=7, amount=74.0),
            turn("counter", 2, actor="seller", action="counter", status_id=1, amount=90.0),
            turn("buyer-counter", 1, actor="seller", action="counter", status_id=7, amount=91.0),
            turn("buyer-counter", 2, actor="buyer", action="counter", status_id=2, amount=80.0),
        ]
        tasks = build_real_tasks_from_records([listing()], rows)

        self.assertCountEqual(
            [row["label"] for row in tasks["seller_next_action"]],
            ["accept", "accept", "decline", "decline", "expire", "counter"],
        )
        self.assertCountEqual(
            [row["label"] for row in tasks["buyer_response_to_counter"]],
            ["accept", "counter"],
        )

    def test_censored_status_is_excluded_not_rejection(self) -> None:
        tasks = build_real_tasks_from_records(
            [listing()],
            [turn("censored", 1, actor="buyer", action="offer", status_id=8, amount=70.0)],
        )

        self.assertEqual(tasks["seller_next_action"], [])
        self.assertEqual(tasks["agreement"], [])

    def test_final_price_ratio_uses_listing_sale_price_not_accepted_turn_amount(self) -> None:
        tasks = build_real_tasks_from_records(
            [listing(final_sale_price=82.0, listing_price=100.0)],
            [turn("accepted", 1, actor="buyer", action="offer", status_id=1, amount=77.0)],
        )

        self.assertEqual(tasks["final_price_ratio"][0]["label"], 0.82)

    def test_response_latency_uses_response_time_and_excludes_negative_latency(self) -> None:
        tasks = build_real_tasks_from_records(
            [listing()],
            [
                turn(
                    "positive",
                    1,
                    actor="buyer",
                    action="offer",
                    status_id=1,
                    amount=70.0,
                    event_time="2020-01-01T00:00:00",
                    response_time="2020-01-01T00:01:30",
                ),
                turn(
                    "negative",
                    1,
                    actor="buyer",
                    action="offer",
                    status_id=1,
                    amount=70.0,
                    event_time="2020-01-01T00:01:30",
                    response_time="2020-01-01T00:00:00",
                ),
            ],
        )

        self.assertEqual([row["label"] for row in tasks["response_latency"]], [90.0])

    def test_sanitized_history_hides_status_and_response_time(self) -> None:
        tasks = build_real_tasks_from_records(
            [listing()],
            [turn("accepted", 1, actor="buyer", action="offer", status_id=1, amount=70.0)],
        )
        row = tasks["seller_next_action"][0]

        self.assertNotIn("status_id", row["features"])
        self.assertNotIn("event_time", row["features"])
        self.assertNotIn("reference_price", row["features"])
        self.assertNotIn("response_time", row["features"])
        self.assertNotIn("event_time", row["observed_history"][0])
        self.assertNotIn("status_id", row["observed_history"][0])
        self.assertNotIn("response_time", row["observed_history"][0])
        self.assertTrue(assert_no_future_leakage([row]))

    def test_censored_status_is_excluded_from_latency(self) -> None:
        tasks = build_real_tasks_from_records(
            [listing()],
            [
                turn(
                    "censored",
                    1,
                    actor="buyer",
                    action="offer",
                    status_id=8,
                    amount=70.0,
                    event_time="2020-01-01T00:00:00",
                    response_time="2020-01-01T01:00:00",
                )
            ],
        )

        self.assertEqual(tasks["response_latency"], [])


if __name__ == "__main__":
    unittest.main()
