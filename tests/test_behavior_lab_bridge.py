from __future__ import annotations

import _bootstrap  # noqa: F401

import json
import tempfile
import unittest
from pathlib import Path

from behavior_lab.bridge import (
    CAMPAIGN_001_ID,
    BridgeValidationError,
    import_snapshot_file,
    load_snapshots,
    prepare_snapshot_file,
    validate_snapshot,
    validate_snapshot_file,
    with_source_hash,
)
from behavior_lab.ledger import DuplicateRecordError, ImmutableLedger


def _raw_snapshot() -> dict:
    return {
        "schema_version": "behavior_lab_campaign_snapshot.v1",
        "campaign_id": CAMPAIGN_001_ID,
        "episode_id": "manual_001",
        "subject_id": "arman",
        "decision_time": "2026-06-20T09:00:00-04:00",
        "observation_cutoff": "2026-06-20T08:59:59-04:00",
        "task_description": "Open the editor and write the first bridge test",
        "available_actions": ["start_now", "defer", "switch_task", "abandon"],
        "pre_decision_features": {
            "task_type": "coding",
            "time_of_day": "morning",
            "fatigue": 1,
            "ambiguity": 1,
            "estimated_minutes": 45,
            "first_step_explicit": True,
            "deadline_hours": 24,
            "recent_context_switches": 2,
            "public_commitment": False,
        },
        "protected_outcome": {
            "started_within_10_minutes": True,
            "start_latency_seconds": 120,
            "worked_for_20_minutes": True,
            "completed_that_day": False,
        },
        "provenance": {"entry_method": "manual"},
    }


class BehaviorLabBridgeTests(unittest.TestCase):
    def test_hash_prepare_validate_and_import_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_path = root / "manual_raw.jsonl"
            hashed_path = root / "export_hashed.jsonl"
            raw_path.write_text(json.dumps(_raw_snapshot()) + "\n", encoding="utf-8")

            prepared = prepare_snapshot_file(raw_path, hashed_path)
            self.assertEqual(len(prepared), 1)
            self.assertIn("source_hash", prepared[0])
            validation = validate_snapshot_file(hashed_path, campaign_id=CAMPAIGN_001_ID)
            self.assertEqual(validation["snapshots"], 1)

            result = import_snapshot_file(hashed_path, data_dir=root / "ledger", campaign_id=CAMPAIGN_001_ID)
            self.assertEqual(result.imported, 1)
            ledger = ImmutableLedger(root / "ledger" / "ledger.jsonl")
            self.assertTrue(ledger.verify_hash_chain())
            episodes = ledger.payloads("decision_episode")
            self.assertEqual(len(episodes), 1)
            self.assertEqual(episodes[0]["later_outcomes"]["started_within_10_minutes"], True)
            self.assertEqual(episodes[0]["data_provenance"]["source_hash"], prepared[0]["source_hash"])

    def test_rejects_source_hash_mismatch(self) -> None:
        snapshot = with_source_hash(_raw_snapshot())
        snapshot["pre_decision_features"]["fatigue"] = 3
        with self.assertRaises(BridgeValidationError):
            validate_snapshot(snapshot, campaign_id=CAMPAIGN_001_ID)

    def test_rejects_outcome_leak_in_pre_decision_features(self) -> None:
        snapshot = _raw_snapshot()
        snapshot["pre_decision_features"]["started_within_10_minutes"] = True
        snapshot = with_source_hash(snapshot)
        with self.assertRaises(BridgeValidationError):
            validate_snapshot(snapshot, campaign_id=CAMPAIGN_001_ID)

    def test_reimport_same_snapshot_is_rejected_by_unique_episode_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "export.jsonl"
            export.write_text(json.dumps(with_source_hash(_raw_snapshot())) + "\n", encoding="utf-8")
            import_snapshot_file(export, data_dir=root / "ledger", campaign_id=CAMPAIGN_001_ID)
            with self.assertRaises(DuplicateRecordError):
                import_snapshot_file(export, data_dir=root / "ledger", campaign_id=CAMPAIGN_001_ID)

    def test_json_envelope_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.json"
            snapshot = with_source_hash(_raw_snapshot())
            path.write_text(json.dumps({"snapshots": [snapshot]}), encoding="utf-8")
            self.assertEqual(load_snapshots(path), [snapshot])


if __name__ == "__main__":
    unittest.main()
