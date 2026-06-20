from __future__ import annotations

import _bootstrap  # noqa: F401

import tempfile
import unittest
from pathlib import Path

from behavior_lab.ledger import ImmutableLedger
from behavior_lab.temporal import TemporalLeakageError, assert_snapshot_is_pre_decision, pre_decision_snapshot, supervised_row
from behavior_lab.worlds import HabitPlusOverrideWorld


class LedgerTemporalTests(unittest.TestCase):
    def test_ledger_hash_chain_and_temporal_firewall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ImmutableLedger(Path(tmp) / "ledger.jsonl")
            world = HabitPlusOverrideWorld(seed=1)
            episode = world.generate_episode()
            ledger.append("decision_episode", episode, record_id=episode.episode_id)
            self.assertTrue(ledger.verify_hash_chain())
            payload = ledger.payloads("decision_episode")[0]
            snapshot = pre_decision_snapshot(payload)
            self.assertNotIn("later_outcomes", snapshot)
            self.assertNotIn("observed_action", snapshot)
            row = supervised_row(payload, "started_within_10_minutes")
            self.assertIsNotNone(row)
            self.assertNotIn("later_outcomes", row["features"])

    def test_firewall_rejects_post_fields(self) -> None:
        with self.assertRaises(TemporalLeakageError):
            assert_snapshot_is_pre_decision({"observed_action": {"action": "start_now"}})


if __name__ == "__main__":
    unittest.main()
