from __future__ import annotations

import _bootstrap  # noqa: F401

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from behavior_lab.ledger import ImmutableLedger
from behavior_lab.offerlab import (
    OFFERLAB_CAMPAIGN_ID,
    OFFERLAB_LEDGER_RECORD_TYPE,
    OfferLabError,
    decision_hash,
    ingest_offerlab_snapshots,
    profit_audit,
    recommend_offer_action,
    sample_offer_decision_snapshot,
    validate_offer_decision_snapshot,
    with_decision_hash,
)


def _snapshot(**overrides: object) -> dict:
    snapshot = sample_offer_decision_snapshot()
    snapshot.update(overrides)
    return snapshot


def _outcome_snapshot(**overrides: object) -> dict:
    snapshot = sample_offer_decision_snapshot()
    snapshot["decision_id"] = str(overrides.pop("decision_id", "outcome_001"))
    snapshot["action_taken"] = {"action": "counter_at_amount", "amount": 760.0}
    snapshot["protected_outcome"] = {
        "offer_accepted": True,
        "countered": True,
        "ignored": False,
        "sold_within_48_hours": True,
        "sold_within_7_days": True,
        "final_sale_price": 760.0,
        "net_contribution_margin": 96.3,
        "days_to_sale": 1.0,
        "unpaid_order": False,
        "returned": False,
    }
    snapshot.update(overrides)
    return snapshot


class OfferLabTests(unittest.TestCase):
    def test_validate_pending_snapshot_and_hash(self) -> None:
        snapshot = validate_offer_decision_snapshot(sample_offer_decision_snapshot())
        hashed = with_decision_hash(snapshot)
        self.assertEqual(hashed["campaign_id"], OFFERLAB_CAMPAIGN_ID)
        self.assertEqual(hashed["decision_hash"], decision_hash(hashed))

    def test_rejects_outcome_leak_in_predecision_context(self) -> None:
        snapshot = sample_offer_decision_snapshot()
        snapshot["pre_decision_context"]["final_sale_price"] = 760.0
        with self.assertRaises(OfferLabError):
            validate_offer_decision_snapshot(snapshot)

    def test_rejects_inconsistent_offer_ratio(self) -> None:
        snapshot = sample_offer_decision_snapshot()
        snapshot["pre_decision_context"]["offer_to_asking_ratio"] = 0.1
        with self.assertRaises(OfferLabError):
            validate_offer_decision_snapshot(snapshot)

    def test_outcome_requires_action_taken(self) -> None:
        snapshot = _outcome_snapshot(action_taken=None)
        with self.assertRaises(OfferLabError):
            validate_offer_decision_snapshot(snapshot)

    def test_recommendation_is_read_only_and_margin_based(self) -> None:
        result = recommend_offer_action(sample_offer_decision_snapshot())
        self.assertFalse(result["execute_action"])
        self.assertEqual(result["recommendation"]["action"], "counter_at_amount")
        self.assertGreater(result["recommendation"]["expected_contribution_margin"], 0)
        self.assertIn("expected_advantage_over_accept_now", result["recommendation"])

    def test_floor_guardrail_marks_actions_below_floor(self) -> None:
        snapshot = sample_offer_decision_snapshot()
        snapshot["pre_decision_context"]["minimum_net_proceeds"] = 700.0
        result = recommend_offer_action(snapshot)
        accept = next(item for item in result["evaluated_actions"] if item["action"] == "accept")
        self.assertTrue(accept["violates_floor"])

    def test_ingest_is_append_only_and_idempotent_for_same_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "history.jsonl"
            source.write_text(json.dumps(_outcome_snapshot()) + "\n", encoding="utf-8")
            first = ingest_offerlab_snapshots(source, data_dir=Path(tmp) / "data")
            second = ingest_offerlab_snapshots(source, data_dir=Path(tmp) / "data")
            ledger = ImmutableLedger(Path(tmp) / "data" / "ledger.jsonl")
            self.assertEqual(first.imported, 1)
            self.assertEqual(second.skipped_existing, 1)
            self.assertEqual(len(ledger.payloads(OFFERLAB_LEDGER_RECORD_TYPE)), 1)
            self.assertTrue(ledger.verify_hash_chain())

    def test_ingest_rejects_same_id_with_different_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.jsonl"
            second = Path(tmp) / "second.jsonl"
            first.write_text(json.dumps(_outcome_snapshot(decision_id="same")) + "\n", encoding="utf-8")
            changed = _outcome_snapshot(decision_id="same")
            changed["protected_outcome"]["net_contribution_margin"] = 12.0
            second.write_text(json.dumps(changed) + "\n", encoding="utf-8")
            ingest_offerlab_snapshots(first, data_dir=Path(tmp) / "data")
            with self.assertRaises(OfferLabError):
                ingest_offerlab_snapshots(second, data_dir=Path(tmp) / "data")

    def test_profit_audit_groups_realized_margin_by_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "history.jsonl"
            source.write_text(
                json.dumps(_outcome_snapshot(decision_id="one")) + "\n"
                + json.dumps(_outcome_snapshot(decision_id="two")) + "\n",
                encoding="utf-8",
            )
            ingest_offerlab_snapshots(source, data_dir=Path(tmp) / "data")
            audit = profit_audit(Path(tmp) / "data")
            self.assertEqual(audit["decisions_with_outcomes"], 2)
            self.assertEqual(audit["by_action"]["counter_at_amount"]["decisions"], 2)
            self.assertEqual(audit["total_net_contribution_margin"], 192.6)

    def test_cli_ingest_audit_and_recommend_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / "history.jsonl"
            pending = root / "pending.json"
            history.write_text(json.dumps(_outcome_snapshot()) + "\n", encoding="utf-8")
            pending.write_text(json.dumps(sample_offer_decision_snapshot()), encoding="utf-8")
            env = dict(os.environ)
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "behavior_lab",
                    "offerlab-ingest",
                    "--input",
                    str(history),
                    "--data-dir",
                    str(root / "data"),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            audit = subprocess.run(
                [sys.executable, "-m", "behavior_lab", "offerlab-audit", "--data-dir", str(root / "data")],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            rec = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "behavior_lab",
                    "offerlab-recommend",
                    "--input",
                    str(pending),
                    "--data-dir",
                    str(root / "data"),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(json.loads(audit.stdout)["decisions"], 1)
            self.assertEqual(json.loads(rec.stdout)["historical_cases_considered"], 1)


if __name__ == "__main__":
    unittest.main()
