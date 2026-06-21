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
    profit_audit_report,
    recommend_offer_action,
    sample_offer_decision_snapshot,
    validate_offer_decision_snapshot,
    with_decision_hash,
)


def _snapshot(**overrides: object) -> dict:
    snapshot = sample_offer_decision_snapshot()
    snapshot.update(overrides)
    return snapshot


def _outcome(**overrides: object) -> dict:
    outcome = {
        "offer_received": True,
        "seller_accepted": True,
        "buyer_paid": True,
        "countered": True,
        "ignored": False,
        "sold_within_48_hours": True,
        "sold_within_7_days": True,
        "final_sale_price": 760.0,
        "days_to_sale": 1.0,
        "unpaid_order": False,
        "order_cancelled": False,
        "returned": False,
        "return_window_matured": True,
        "actual_ebay_fees": 100.7,
        "provisional_margin": 96.3,
        "mature_margin": 96.3,
        "margin_maturity_date": "2026-07-01",
    }
    outcome.update(overrides)
    return outcome


def _outcome_snapshot(
    *,
    decision_id: str = "outcome_001",
    action_taken: dict | None = None,
    context_overrides: dict | None = None,
    outcome_overrides: dict | None = None,
    available_actions: list[dict] | None = None,
) -> dict:
    snapshot = sample_offer_decision_snapshot()
    snapshot["decision_id"] = decision_id
    snapshot["listing_id"] = f"listing_{decision_id}"
    snapshot["pre_decision_context"]["listing_id"] = snapshot["listing_id"]
    if context_overrides:
        snapshot["pre_decision_context"].update(context_overrides)
    if available_actions is not None:
        snapshot["available_actions"] = available_actions
    snapshot["action_taken"] = action_taken or {"action": "counter_at_amount", "amount": 760.0}
    snapshot["protected_outcome"] = _outcome(**(outcome_overrides or {}))
    return snapshot


def _write_jsonl(path: Path, snapshots: list[dict]) -> None:
    path.write_text("".join(json.dumps(snapshot) + "\n" for snapshot in snapshots), encoding="utf-8")


def _mature_history(count: int, *, decision_channel: str = "buyer_initiated_best_offer") -> list[dict]:
    rows = []
    for index in range(count):
        rows.append(
            _outcome_snapshot(
                decision_id=f"hist_{decision_channel}_{index}",
                context_overrides={
                    "decision_channel": decision_channel,
                    "asking_price": 900.0,
                    "buyer_offer_amount": 720.0 if decision_channel == "buyer_initiated_best_offer" else None,
                    "offer_to_asking_ratio": 0.8 if decision_channel == "buyer_initiated_best_offer" else None,
                    "traffic_data_age_hours": 12.0,
                },
                outcome_overrides={
                    "final_sale_price": 760.0,
                    "actual_ebay_fees": 100.7,
                    "provisional_margin": 96.3 + index,
                    "mature_margin": 96.3 + index,
                },
            )
        )
    return rows


class OfferLabTests(unittest.TestCase):
    def test_validate_pending_snapshot_and_hash(self) -> None:
        snapshot = validate_offer_decision_snapshot(sample_offer_decision_snapshot())
        hashed = with_decision_hash(snapshot)
        self.assertEqual(hashed["campaign_id"], OFFERLAB_CAMPAIGN_ID)
        self.assertEqual(snapshot["schema_version"], "offerlab_decision_snapshot.v2")
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

    def test_cost_basis_may_be_null_but_recommendation_abstains(self) -> None:
        snapshot = sample_offer_decision_snapshot()
        snapshot["pre_decision_context"]["seller_cost_basis"] = None
        validate_offer_decision_snapshot(snapshot)
        result = recommend_offer_action(snapshot)
        self.assertEqual(result["recommendation"]["status"], "abstain")
        self.assertIn("missing_seller_cost_basis", result["recommendation"]["reasons"])

    def test_outcome_requires_action_taken(self) -> None:
        snapshot = _outcome_snapshot()
        snapshot["action_taken"] = None
        with self.assertRaises(OfferLabError):
            validate_offer_decision_snapshot(snapshot)

    def test_immature_outcome_rejects_mature_margin(self) -> None:
        snapshot = _outcome_snapshot(
            outcome_overrides={
                "return_window_matured": False,
                "mature_margin": 96.3,
            }
        )
        with self.assertRaises(OfferLabError):
            validate_offer_decision_snapshot(snapshot)

    def test_recommendation_abstains_with_insufficient_mature_comparables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "history.jsonl"
            _write_jsonl(source, [_outcome_snapshot()])
            ingest_offerlab_snapshots(source, data_dir=Path(tmp) / "data")
            result = recommend_offer_action(sample_offer_decision_snapshot(), data_dir=Path(tmp) / "data")
            self.assertEqual(result["recommendation"]["status"], "abstain")
            self.assertIn("insufficient_comparable_mature_outcomes", result["recommendation"]["reasons"])
            self.assertEqual(result["comparable_mature_cases_considered"], 1)

    def test_recommendation_can_pass_evidence_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "history.jsonl"
            _write_jsonl(source, _mature_history(10))
            ingest_offerlab_snapshots(source, data_dir=Path(tmp) / "data")
            result = recommend_offer_action(sample_offer_decision_snapshot(), data_dir=Path(tmp) / "data")
            self.assertEqual(result["recommendation"]["status"], "recommend")
            self.assertEqual(result["recommendation"]["action"], "counter_at_amount")
            self.assertEqual(result["comparable_mature_cases_considered"], 10)
            self.assertFalse(result["execute_action"])

    def test_recommendation_keeps_decision_channels_separate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "history.jsonl"
            _write_jsonl(source, _mature_history(10, decision_channel="seller_initiated_offer"))
            ingest_offerlab_snapshots(source, data_dir=Path(tmp) / "data")
            result = recommend_offer_action(sample_offer_decision_snapshot(), data_dir=Path(tmp) / "data")
            self.assertEqual(result["recommendation"]["status"], "abstain")
            self.assertIn("insufficient_comparable_mature_outcomes", result["recommendation"]["reasons"])
            self.assertEqual(result["comparable_mature_cases_considered"], 0)

    def test_stale_traffic_forces_abstention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "history.jsonl"
            _write_jsonl(source, _mature_history(10))
            ingest_offerlab_snapshots(source, data_dir=Path(tmp) / "data")
            snapshot = sample_offer_decision_snapshot()
            snapshot["pre_decision_context"]["traffic_data_age_hours"] = 96.0
            result = recommend_offer_action(snapshot, data_dir=Path(tmp) / "data")
            self.assertEqual(result["recommendation"]["status"], "abstain")
            self.assertIn("stale_traffic_data", result["recommendation"]["reasons"])

    def test_ingest_is_append_only_and_idempotent_for_same_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "history.jsonl"
            _write_jsonl(source, [_outcome_snapshot()])
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
            _write_jsonl(first, [_outcome_snapshot(decision_id="same")])
            changed = _outcome_snapshot(decision_id="same")
            changed["protected_outcome"]["mature_margin"] = 12.0
            _write_jsonl(second, [changed])
            ingest_offerlab_snapshots(first, data_dir=Path(tmp) / "data")
            with self.assertRaises(OfferLabError):
                ingest_offerlab_snapshots(second, data_dir=Path(tmp) / "data")

    def test_profit_audit_uses_paid_mature_margin_not_seller_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "history.jsonl"
            unpaid = _outcome_snapshot(
                decision_id="unpaid",
                action_taken={"action": "accept", "amount": 720.0},
                outcome_overrides={
                    "seller_accepted": True,
                    "buyer_paid": False,
                    "countered": False,
                    "sold_within_48_hours": False,
                    "sold_within_7_days": False,
                    "final_sale_price": None,
                    "days_to_sale": None,
                    "unpaid_order": True,
                    "return_window_matured": False,
                    "actual_ebay_fees": None,
                    "provisional_margin": None,
                    "mature_margin": None,
                    "margin_maturity_date": None,
                },
            )
            _write_jsonl(source, [_outcome_snapshot(decision_id="paid"), unpaid])
            ingest_offerlab_snapshots(source, data_dir=Path(tmp) / "data")
            audit = profit_audit(Path(tmp) / "data")
            self.assertEqual(audit["decisions_with_outcomes"], 2)
            self.assertEqual(audit["paid_outcomes"], 1)
            self.assertEqual(audit["mature_paid_outcomes"], 1)
            self.assertEqual(audit["seller_accepted_unpaid_outcomes"], 1)
            self.assertEqual(audit["total_mature_contribution_margin"], 96.3)

    def test_profit_audit_report_has_required_sections_and_quality_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "history.jsonl"
            _write_jsonl(source, [_outcome_snapshot(decision_id="one"), _outcome_snapshot(decision_id="two")])
            ingest_offerlab_snapshots(source, data_dir=Path(tmp) / "data")
            report = profit_audit_report(Path(tmp) / "data")
            self.assertIn("historical_policy_audit", report)
            self.assertIn("profit_frontier", report)
            self.assertIn("missed_opportunities", report)
            self.assertIn("proposed_policy", report)
            self.assertIn("prospective_test", report)
            self.assertIn("data_quality", report)
            self.assertIn("fewer than 30 mature paid outcomes", report["data_quality"]["warnings"])

    def test_cli_ingest_report_audit_and_recommend_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / "history.jsonl"
            pending = root / "pending.json"
            report_path = root / "report.md"
            _write_jsonl(history, [_outcome_snapshot()])
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
            report = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "behavior_lab",
                    "offerlab-report",
                    "--data-dir",
                    str(root / "data"),
                    "--output",
                    str(report_path),
                ],
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
            self.assertTrue(report_path.exists())
            self.assertIn("# OfferLab Profit Audit", report_path.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(report.stdout)["campaign_id"], OFFERLAB_CAMPAIGN_ID)
            self.assertEqual(json.loads(rec.stdout)["recommendation"]["status"], "abstain")


if __name__ == "__main__":
    unittest.main()
