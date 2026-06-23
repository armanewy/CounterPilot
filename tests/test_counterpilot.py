from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import _bootstrap  # noqa: F401

from behavior_lab.counterpilot import (
    CounterpilotError,
    ingest_counterpilot_events,
    counterpilot_audit,
    counterpilot_experiment_assign,
    counterpilot_experiment_preregister,
    counterpilot_experiment_record_outcome,
    counterpilot_experiment_report,
    counterpilot_inbox,
    counterpilot_rule_simulation,
    counterpilot_shadow_recommend,
    counterpilot_utility_report,
    sample_counterpilot_events,
    validate_counterpilot_event,
    write_counterpilot_templates,
)


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


class CounterpilotTests(unittest.TestCase):
    def test_templates_include_consent_and_month_one_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = write_counterpilot_templates(Path(tmp) / "templates")
            self.assertEqual(manifest["product_id"], "counterpilot_negotiated_commerce")
            self.assertIn("merchant_consent", manifest["events"])
            self.assertFalse(manifest["data_rights"]["cross_merchant_pooling_default"])
            self.assertIn("offer and quote event capture", manifest["month_1_scope"])

    def test_ingest_inbox_accounting_and_audit_are_consent_gated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, [events["merchant_consent"], events["offer_opened"]])
            result = ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")
            self.assertEqual(result.imported, 2)

            inbox = counterpilot_inbox(Path(tmp) / "data", merchant_id="merchant_demo_refurb_tech")
            self.assertEqual(inbox["open_offer_count"], 1)
            self.assertFalse(inbox["executes_seller_actions"])
            economics = inbox["open_offers"][0]["economics"]
            accept = next(row for row in economics if row["action"] == "accept")
            self.assertEqual(accept["mature_margin_if_sold"], 114.82)
            self.assertFalse(accept["violates_merchant_floor"])
            self.assertTrue(inbox["open_offers"][0]["merchant_specific_learning_authorized"])

            audit = counterpilot_audit(Path(tmp) / "data", merchant_id="merchant_demo_refurb_tech")
            self.assertFalse(audit["profit_optimization_gate"]["passed"])
            self.assertTrue(audit["profit_optimization_gate"]["checks"]["merchant_specific_learning_consent"])
            self.assertFalse(audit["automation_allowed"])
            self.assertEqual(audit["model_training"], "not_run")

    def test_free_shipping_counter_keeps_merchant_shipping_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            events["offer_opened"]["available_actions"].append({"action": "free_shipping_counter", "amount": 760.0})
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, [events["merchant_consent"], events["offer_opened"]])
            ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")

            inbox = counterpilot_inbox(Path(tmp) / "data", merchant_id="merchant_demo_refurb_tech")
            economics = inbox["open_offers"][0]["economics"]
            free_shipping = next(row for row in economics if row["action"] == "free_shipping_counter")

            self.assertEqual(free_shipping["mature_margin_if_sold"], 153.66)
            self.assertNotEqual(free_shipping["mature_margin_if_sold"], 187.66)

    def test_audit_reports_mature_margin_without_training(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, list(events.values()))
            ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")

            audit = counterpilot_audit(Path(tmp) / "data", merchant_id="merchant_demo_refurb_tech")

            self.assertEqual(audit["counts"]["mature_paid_outcomes"], 1)
            self.assertEqual(audit["mature_contribution_margin"]["total"], 171.66)
            self.assertFalse(audit["data_rights"]["cross_merchant_pooling_authorized"])
            self.assertEqual(audit["current_stage"], "transaction_surface")

    def test_utility_report_summarizes_reconciled_merchant_economics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, list(events.values()))
            ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")

            report = counterpilot_utility_report(Path(tmp) / "data", merchant_id="merchant_demo_refurb_tech")

            self.assertFalse(report["causal_claim"])
            self.assertEqual(report["model_training"], "not_run")
            funnel = report["offer_volume_and_acceptance_funnel"]
            self.assertEqual(funnel["offers_opened"], 1)
            self.assertEqual(funnel["accepted_or_countered"], 1)
            self.assertEqual(funnel["paid_nonreturned_mature_outcomes"], 1)
            self.assertEqual(report["refund_return_adjusted_margin"]["gross_paid_sales"], 760.0)
            self.assertEqual(report["refund_return_adjusted_margin"]["mature_contribution_margin"], 171.66)
            self.assertEqual(report["amount_conceded_vs_asking"]["average_concession"], 140.0)
            self.assertEqual(report["time_from_offer_to_payment"]["average_days"], 4.3)
            self.assertIn("matured into contribution margin", report["merchant_value_statement"])

    def test_fixed_rule_simulation_is_historical_and_not_causal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, list(events.values()))
            ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")

            simulation = counterpilot_rule_simulation(
                Path(tmp) / "data",
                merchant_id="merchant_demo_refurb_tech",
                rule={"rule_type": "counter_percent_above_offer", "counter_markup_pct": 0.0556},
            )

            self.assertTrue(simulation["not_causal"])
            self.assertFalse(simulation["causal_claim"])
            self.assertEqual(simulation["model_training"], "not_run")
            self.assertEqual(simulation["summary"]["eligible_offers"], 1)
            self.assertEqual(simulation["summary"]["action_counts"], {"counter_at_amount": 1})
            self.assertEqual(simulation["summary"]["matched_actual_actions"], 1)
            self.assertTrue(simulation["rows"][0]["observed_outcome_reused"])

    def test_utility_report_does_not_label_declined_outcomes_as_accepted_margin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            events["merchant_decision"]["selected_action"] = {"action": "decline"}
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, list(events.values()))
            ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")

            report = counterpilot_utility_report(Path(tmp) / "data", merchant_id="merchant_demo_refurb_tech")

            self.assertEqual(report["offer_volume_and_acceptance_funnel"]["accepted_or_countered"], 0)
            self.assertEqual(report["mature_margin_per_accepted_offer"], [])
            self.assertEqual(report["merchant_value_statement"], "No accepted offers have mature paid outcomes yet.")

    def test_rule_simulation_does_not_reuse_outcome_when_actions_differ(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            events["merchant_decision"]["selected_action"] = {"action": "decline"}
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, list(events.values()))
            ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")

            simulation = counterpilot_rule_simulation(
                Path(tmp) / "data",
                merchant_id="merchant_demo_refurb_tech",
                rule={"rule_type": "counter_percent_above_offer", "counter_markup_pct": 0.0556},
            )

            self.assertFalse(simulation["rows"][0]["actions_match"])
            self.assertFalse(simulation["rows"][0]["observed_outcome_reused"])
            self.assertIsNone(simulation["rows"][0]["observed_mature_margin"])
            self.assertEqual(simulation["rows"][0]["observed_outcome_reuse_reason"], "not_reused_for_counterfactual_action")

    def test_shadow_recommendation_records_transparent_counter_without_automation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            current_offer = json.loads(json.dumps(events["offer_opened"]))
            current_offer["event_id"] = "offer_current_001"
            current_offer["offer_id"] = "offer_current_001"
            current_offer["occurred_at"] = "2026-07-23T10:00:00-04:00"
            current_offer["observation_cutoff"] = "2026-07-23T10:00:00-04:00"
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, [events["merchant_consent"], events["offer_opened"], events["merchant_decision"], events["outcome_matured"], current_offer])
            ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")

            recommendation = counterpilot_shadow_recommend(
                Path(tmp) / "data",
                merchant_id="merchant_demo_refurb_tech",
                offer_id="offer_current_001",
                config={"minimum_comparable_mature_outcomes": 1, "floor_buffer": 60.0},
                generated_at="2026-07-23T10:01:00-04:00",
            )

            self.assertEqual(recommendation["system_mode"], "shadow_only")
            self.assertFalse(recommendation["automation_allowed"])
            self.assertEqual(recommendation["model_training"], "not_run")
            self.assertTrue(recommendation["no_customer_targeting"])
            self.assertEqual(recommendation["recommendation"]["action"], "counter_at_amount")
            self.assertEqual(recommendation["recommendation"]["amount"], 760.0)
            self.assertEqual(recommendation["evidence"]["comparable_mature_outcomes"], 1)

            duplicate = counterpilot_shadow_recommend(
                Path(tmp) / "data",
                merchant_id="merchant_demo_refurb_tech",
                offer_id="offer_current_001",
                config={"minimum_comparable_mature_outcomes": 1, "floor_buffer": 60.0},
                generated_at="2026-07-23T10:02:00-04:00",
            )
            self.assertEqual(duplicate["recommendation_id"], recommendation["recommendation_id"])
            with self.assertRaises(CounterpilotError):
                counterpilot_shadow_recommend(
                    Path(tmp) / "data",
                    merchant_id="merchant_demo_refurb_tech",
                    offer_id="offer_current_001",
                    config={"minimum_comparable_mature_outcomes": 1, "floor_buffer": 9999.0},
                    generated_at="2026-07-23T10:03:00-04:00",
                )

            audit_events = counterpilot_utility_report(Path(tmp) / "data", merchant_id="merchant_demo_refurb_tech")
            self.assertEqual(audit_events["offer_volume_and_acceptance_funnel"]["offers_opened"], 2)

    def test_shadow_recommendation_internal_id_does_not_trip_pii_scanner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            current_offer = json.loads(json.dumps(events["offer_opened"]))
            current_offer["event_id"] = "offer_config_change"
            current_offer["offer_id"] = "offer_config_change"
            current_offer["occurred_at"] = "2026-07-23T10:00:00-04:00"
            current_offer["observation_cutoff"] = "2026-07-23T10:00:00-04:00"
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, [events["merchant_consent"], events["offer_opened"], events["merchant_decision"], events["outcome_matured"], current_offer])
            ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")

            recommendation = counterpilot_shadow_recommend(
                Path(tmp) / "data",
                merchant_id="merchant_demo_refurb_tech",
                offer_id="offer_config_change",
                config={"minimum_comparable_mature_outcomes": 1, "floor_buffer": 60.0},
                generated_at="2026-07-23T10:01:00-04:00",
                append=False,
            )
            self.assertTrue(recommendation["recommendation_id"].startswith("shadow_"))

    def test_shadow_recommendation_cannot_append_after_merchant_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, [events["merchant_consent"], events["offer_opened"], events["merchant_decision"]])
            ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")

            with self.assertRaises(CounterpilotError):
                counterpilot_shadow_recommend(
                    Path(tmp) / "data",
                    merchant_id="merchant_demo_refurb_tech",
                    offer_id="offer_demo_001",
                    generated_at="2026-06-22T10:06:00-04:00",
                )

            preview = counterpilot_shadow_recommend(
                Path(tmp) / "data",
                merchant_id="merchant_demo_refurb_tech",
                offer_id="offer_demo_001",
                generated_at="2026-06-22T10:06:00-04:00",
                append=False,
            )
            self.assertEqual(preview["recommendation"]["action"], "abstain")
            self.assertIn("merchant_decision_already_recorded", preview["abstention_reasons"])

    def test_shadow_recommendation_abstains_for_missing_cost_basis_or_sensitive_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            current_offer = json.loads(json.dumps(events["offer_opened"]))
            current_offer["event_id"] = "offer_current_missing_cost"
            current_offer["offer_id"] = "offer_current_missing_cost"
            current_offer["occurred_at"] = "2026-07-23T10:00:00-04:00"
            current_offer["observation_cutoff"] = "2026-07-23T10:00:00-04:00"
            current_offer["pre_decision_context"]["cost_basis"] = None
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, [events["merchant_consent"], events["offer_opened"], events["merchant_decision"], events["outcome_matured"], current_offer])
            ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")

            missing_cost = counterpilot_shadow_recommend(
                Path(tmp) / "data",
                merchant_id="merchant_demo_refurb_tech",
                offer_id="offer_current_missing_cost",
                config={"minimum_comparable_mature_outcomes": 1},
                generated_at="2026-07-23T10:01:00-04:00",
                append=False,
            )

            self.assertEqual(missing_cost["recommendation"]["action"], "abstain")
            self.assertIn("cost_basis_missing", missing_cost["abstention_reasons"])

            sensitive_offer = json.loads(json.dumps(events["offer_opened"]))
            sensitive_offer["event_id"] = "offer_current_sensitive"
            sensitive_offer["offer_id"] = "offer_current_sensitive"
            sensitive_offer["occurred_at"] = "2026-07-23T10:02:00-04:00"
            sensitive_offer["observation_cutoff"] = "2026-07-23T10:02:00-04:00"
            sensitive_offer["pre_decision_context"]["buyer_zip"] = "10001"
            sensitive_path = Path(tmp) / "sensitive.json"
            _write_jsonl(sensitive_path, [sensitive_offer])
            ingest_counterpilot_events(sensitive_path, data_dir=Path(tmp) / "data")

            sensitive = counterpilot_shadow_recommend(
                Path(tmp) / "data",
                merchant_id="merchant_demo_refurb_tech",
                offer_id="offer_current_sensitive",
                config={"minimum_comparable_mature_outcomes": 1},
                generated_at="2026-07-23T10:03:00-04:00",
                append=False,
            )
            self.assertEqual(sensitive["recommendation"]["action"], "abstain")
            self.assertIn("sensitive_or_customer_targeting_feature_present", sensitive["abstention_reasons"])

            protected_offer = json.loads(json.dumps(events["offer_opened"]))
            protected_offer["event_id"] = "offer_current_protected"
            protected_offer["offer_id"] = "offer_current_protected"
            protected_offer["occurred_at"] = "2026-07-23T10:04:00-04:00"
            protected_offer["observation_cutoff"] = "2026-07-23T10:04:00-04:00"
            protected_offer["pre_decision_context"]["buyer_age"] = 72
            protected_path = Path(tmp) / "protected.json"
            _write_jsonl(protected_path, [protected_offer])
            ingest_counterpilot_events(protected_path, data_dir=Path(tmp) / "data")

            protected = counterpilot_shadow_recommend(
                Path(tmp) / "data",
                merchant_id="merchant_demo_refurb_tech",
                offer_id="offer_current_protected",
                config={"minimum_comparable_mature_outcomes": 1},
                generated_at="2026-07-23T10:05:00-04:00",
                append=False,
            )
            self.assertEqual(protected["recommendation"]["action"], "abstain")
            self.assertIn("sensitive_or_customer_targeting_feature_present", protected["abstention_reasons"])

    def test_experiment_shadow_recommendation_exposure_preregisters_assigns_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            current_offer = json.loads(json.dumps(events["offer_opened"]))
            current_offer["event_id"] = "offer_experiment_001"
            current_offer["offer_id"] = "offer_experiment_001"
            current_offer["occurred_at"] = "2026-07-23T10:00:00-04:00"
            current_offer["observation_cutoff"] = "2026-07-23T10:00:00-04:00"
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, [events["merchant_consent"], current_offer])
            ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")

            prereg = counterpilot_experiment_preregister(
                Path(tmp) / "data",
                experiment_id="exp_shadow_adoption_001",
                experiment_type="shadow_recommendation_exposure",
                merchant_id="merchant_demo_refurb_tech",
                planned_units=4,
                assignment_probability=0.5,
            )
            duplicate_prereg = counterpilot_experiment_preregister(
                Path(tmp) / "data",
                experiment_id="exp_shadow_adoption_001",
                experiment_type="shadow_recommendation_exposure",
                merchant_id="merchant_demo_refurb_tech",
                planned_units=4,
                assignment_probability=0.5,
            )
            self.assertEqual(duplicate_prereg["preregistration_hash"], prereg["preregistration_hash"])
            with self.assertRaises(CounterpilotError):
                counterpilot_experiment_assign(
                    Path(tmp) / "data",
                    experiment_id=prereg["experiment_id"],
                    merchant_id="merchant_demo_refurb_tech",
                    offer_id="offer_experiment_001",
                    assigned_at="2026-06-23T10:01:00-04:00",
                )
            assignment = counterpilot_experiment_assign(
                Path(tmp) / "data",
                experiment_id=prereg["experiment_id"],
                merchant_id="merchant_demo_refurb_tech",
                offer_id="offer_experiment_001",
                assigned_at="2026-07-23T10:01:00-04:00",
            )
            duplicate = counterpilot_experiment_assign(
                Path(tmp) / "data",
                experiment_id=prereg["experiment_id"],
                merchant_id="merchant_demo_refurb_tech",
                offer_id="offer_experiment_001",
                assigned_at="2026-07-23T10:02:00-04:00",
            )
            self.assertEqual(duplicate["assignment_id"], assignment["assignment_id"])
            decision = json.loads(json.dumps(events["merchant_decision"]))
            decision["event_id"] = "decision_experiment_001"
            decision["offer_id"] = "offer_experiment_001"
            decision["occurred_at"] = "2026-07-23T10:05:00-04:00"
            decision_path = Path(tmp) / "decision.jsonl"
            _write_jsonl(decision_path, [decision])
            ingest_counterpilot_events(decision_path, data_dir=Path(tmp) / "data")
            replay_after_decision = counterpilot_experiment_assign(
                Path(tmp) / "data",
                experiment_id=prereg["experiment_id"],
                merchant_id="merchant_demo_refurb_tech",
                offer_id="offer_experiment_001",
                assigned_at="2026-07-23T10:06:00-04:00",
            )
            self.assertEqual(replay_after_decision["assignment_id"], assignment["assignment_id"])
            outcome = counterpilot_experiment_record_outcome(
                Path(tmp) / "data",
                assignment_id=assignment["assignment_id"],
                outcomes={"merchant_adopted_recommendation": True},
                recorded_at="2026-07-23T10:20:00-04:00",
            )
            self.assertEqual(outcome["primary_outcome"], "merchant_adopted_recommendation")
            report = counterpilot_experiment_report(Path(tmp) / "data", experiment_id=prereg["experiment_id"])
            self.assertEqual(report["assignments"]["total"], 1)
            self.assertEqual(report["outcomes_recorded"], 1)
            self.assertEqual(report["analysis_population"], "available_mature_outcomes")
            self.assertEqual(report["missing_outcome_count"], 0)
            self.assertFalse(report["automation_allowed"])
            self.assertEqual(report["model_training"], "not_run")

    def test_offer_policy_experiment_requires_guardrails_and_blocks_sensitive_targeting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            current_offer = json.loads(json.dumps(events["offer_opened"]))
            current_offer["event_id"] = "offer_policy_001"
            current_offer["offer_id"] = "offer_policy_001"
            current_offer["occurred_at"] = "2026-07-23T10:00:00-04:00"
            current_offer["observation_cutoff"] = "2026-07-23T10:00:00-04:00"
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, [events["merchant_consent"], current_offer])
            ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")

            with self.assertRaises(CounterpilotError):
                counterpilot_experiment_preregister(
                    Path(tmp) / "data",
                    experiment_id="exp_policy_missing_guardrails",
                    experiment_type="offer_policy_comparison",
                    merchant_id="merchant_demo_refurb_tech",
                    planned_units=10,
                )
            prereg = counterpilot_experiment_preregister(
                Path(tmp) / "data",
                experiment_id="exp_policy_margin_001",
                experiment_type="offer_policy_comparison",
                merchant_id="merchant_demo_refurb_tech",
                planned_units=10,
                guardrails={"minimum_net_floor": 75.0, "maximum_concession_rate": 0.25},
            )
            assignment = counterpilot_experiment_assign(
                Path(tmp) / "data",
                experiment_id=prereg["experiment_id"],
                merchant_id="merchant_demo_refurb_tech",
                offer_id="offer_policy_001",
                assigned_at="2026-07-23T10:01:00-04:00",
            )
            outcome = counterpilot_experiment_record_outcome(
                Path(tmp) / "data",
                assignment_id=assignment["assignment_id"],
                outcomes={"mature_contribution_margin_per_eligible_negotiation": 171.66},
                recorded_at="2026-08-23T10:01:00-04:00",
            )
            self.assertEqual(outcome["primary_outcome"], "mature_contribution_margin_per_eligible_negotiation")

            sensitive_offer = json.loads(json.dumps(events["offer_opened"]))
            sensitive_offer["event_id"] = "offer_policy_sensitive"
            sensitive_offer["offer_id"] = "offer_policy_sensitive"
            sensitive_offer["occurred_at"] = "2026-07-23T10:02:00-04:00"
            sensitive_offer["observation_cutoff"] = "2026-07-23T10:02:00-04:00"
            sensitive_offer["pre_decision_context"]["buyer_gender"] = "unknown"
            sensitive_path = Path(tmp) / "sensitive_policy.jsonl"
            _write_jsonl(sensitive_path, [sensitive_offer])
            ingest_counterpilot_events(sensitive_path, data_dir=Path(tmp) / "data")
            with self.assertRaises(CounterpilotError):
                counterpilot_experiment_assign(
                    Path(tmp) / "data",
                    experiment_id=prereg["experiment_id"],
                    merchant_id="merchant_demo_refurb_tech",
                    offer_id="offer_policy_sensitive",
                )

    def test_shadow_experiment_control_holdout_blocks_shadow_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            current_offer = json.loads(json.dumps(events["offer_opened"]))
            current_offer["event_id"] = "offer_holdout_001"
            current_offer["offer_id"] = "offer_holdout_001"
            current_offer["occurred_at"] = "2026-07-23T10:00:00-04:00"
            current_offer["observation_cutoff"] = "2026-07-23T10:00:00-04:00"
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, [events["merchant_consent"], events["offer_opened"], events["merchant_decision"], events["outcome_matured"], current_offer])
            ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")
            prereg = counterpilot_experiment_preregister(
                Path(tmp) / "data",
                experiment_id="exp_shadow_holdout_001",
                experiment_type="shadow_recommendation_exposure",
                merchant_id="merchant_demo_refurb_tech",
                planned_units=5,
                assignment_probability=0.0 + 1e-9,
            )
            assignment = counterpilot_experiment_assign(
                Path(tmp) / "data",
                experiment_id=prereg["experiment_id"],
                merchant_id="merchant_demo_refurb_tech",
                offer_id="offer_holdout_001",
                assigned_at="2026-07-23T10:01:00-04:00",
            )
            self.assertEqual(assignment["assigned_arm"], "control")
            with self.assertRaises(CounterpilotError):
                counterpilot_shadow_recommend(
                    Path(tmp) / "data",
                    merchant_id="merchant_demo_refurb_tech",
                    offer_id="offer_holdout_001",
                    config={"minimum_comparable_mature_outcomes": 1, "floor_buffer": 60.0},
                    generated_at="2026-07-23T10:02:00-04:00",
                )

            second = counterpilot_experiment_preregister(
                Path(tmp) / "data",
                experiment_id="exp_shadow_overlap_001",
                experiment_type="shadow_recommendation_exposure",
                merchant_id="merchant_demo_refurb_tech",
                planned_units=5,
                assignment_probability=0.999999999,
            )
            second_assignment = counterpilot_experiment_assign(
                Path(tmp) / "data",
                experiment_id=second["experiment_id"],
                merchant_id="merchant_demo_refurb_tech",
                offer_id="offer_holdout_001",
                assigned_at="2026-07-23T10:03:00-04:00",
            )
            self.assertEqual(second_assignment["assigned_arm"], "treatment")
            with self.assertRaises(CounterpilotError):
                counterpilot_shadow_recommend(
                    Path(tmp) / "data",
                    merchant_id="merchant_demo_refurb_tech",
                    offer_id="offer_holdout_001",
                    config={"minimum_comparable_mature_outcomes": 1, "floor_buffer": 60.0},
                    generated_at="2026-07-23T10:04:00-04:00",
                )

    def test_experiment_assignment_must_precede_shadow_recommendation_exposure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            current_offer = json.loads(json.dumps(events["offer_opened"]))
            current_offer["event_id"] = "offer_exposure_first"
            current_offer["offer_id"] = "offer_exposure_first"
            current_offer["occurred_at"] = "2026-07-23T10:00:00-04:00"
            current_offer["observation_cutoff"] = "2026-07-23T10:00:00-04:00"
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, [events["merchant_consent"], events["offer_opened"], events["merchant_decision"], events["outcome_matured"], current_offer])
            ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")
            counterpilot_shadow_recommend(
                Path(tmp) / "data",
                merchant_id="merchant_demo_refurb_tech",
                offer_id="offer_exposure_first",
                config={"minimum_comparable_mature_outcomes": 1, "floor_buffer": 60.0},
                generated_at="2026-07-23T10:01:00-04:00",
            )
            prereg = counterpilot_experiment_preregister(
                Path(tmp) / "data",
                experiment_id="exp_shadow_after_exposure",
                experiment_type="shadow_recommendation_exposure",
                merchant_id="merchant_demo_refurb_tech",
                planned_units=5,
            )
            with self.assertRaises(CounterpilotError):
                counterpilot_experiment_assign(
                    Path(tmp) / "data",
                    experiment_id=prereg["experiment_id"],
                    merchant_id="merchant_demo_refurb_tech",
                    offer_id="offer_exposure_first",
                    assigned_at="2026-07-23T10:02:00-04:00",
                )

    def test_inbox_scopes_consent_and_decisions_by_merchant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            merchant_a_consent = events["merchant_consent"]
            merchant_a_offer = events["offer_opened"]
            merchant_a_decision = events["merchant_decision"]
            merchant_b_offer = json.loads(json.dumps(events["offer_opened"]))
            merchant_b_offer["event_id"] = "offer_demo_001_b"
            merchant_b_offer["merchant_id"] = "merchant_without_consent"
            merchant_b_offer["listing_id"] = "sku_refurb_pc_002"
            merchant_b_offer["pre_decision_context"]["listing_id"] = "sku_refurb_pc_002"

            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, [merchant_a_consent, merchant_a_offer, merchant_a_decision, merchant_b_offer])
            ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")

            inbox = counterpilot_inbox(Path(tmp) / "data")

            self.assertEqual(inbox["open_offer_count"], 1)
            self.assertEqual(inbox["open_offers"][0]["merchant_id"], "merchant_without_consent")
            self.assertFalse(inbox["open_offers"][0]["merchant_specific_learning_authorized"])

    def test_audit_blocks_cross_merchant_pooling_and_bad_threads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_counterpilot_events()
            merchant_a_consent = events["merchant_consent"]
            merchant_a_offer = events["offer_opened"]
            merchant_b_offer = json.loads(json.dumps(events["offer_opened"]))
            merchant_b_offer["event_id"] = "offer_demo_002"
            merchant_b_offer["offer_id"] = "offer_demo_002"
            merchant_b_offer["merchant_id"] = "merchant_b"
            merchant_b_offer["listing_id"] = "sku_refurb_pc_002"
            merchant_b_offer["pre_decision_context"]["listing_id"] = "sku_refurb_pc_002"
            bad_decision = json.loads(json.dumps(events["merchant_decision"]))
            bad_decision["event_id"] = "decision_bad_manual_other"
            bad_decision["selected_action"] = {"action": "manual_other"}

            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, [merchant_a_consent, merchant_a_offer, merchant_b_offer, bad_decision])
            ingest_counterpilot_events(source, data_dir=Path(tmp) / "data")

            aggregate = counterpilot_audit(Path(tmp) / "data")
            merchant_a = counterpilot_audit(Path(tmp) / "data", merchant_id="merchant_demo_refurb_tech")

            self.assertFalse(aggregate["profit_optimization_gate"]["checks"]["single_merchant_namespace"])
            self.assertFalse(aggregate["data_rights"]["merchant_specific_learning_authorized"])
            self.assertFalse(merchant_a["profit_optimization_gate"]["checks"]["event_thread_integrity"])
            self.assertIn("unavailable action", merchant_a["profit_optimization_gate"]["event_thread_integrity"]["errors"][0])

    def test_rejects_customer_pii_and_post_decision_context(self) -> None:
        validate_counterpilot_event(sample_counterpilot_events()["offer_opened"])

        event = sample_counterpilot_events()["offer_opened"]
        event["pre_decision_context"]["buyer_email"] = "person@example.com"
        with self.assertRaises(CounterpilotError):
            validate_counterpilot_event(event)

        pii_cases = [
            ("buyer", {"id": "buyer_123"}),
            ("shopify_customer_gid", "gid://shopify/Customer/123"),
            ("buyer_handle", "repeat-customer"),
            ("contact_email", "person@example.com"),
            ("buyer_note", "interested in this item"),
            ("quote_context", "please email me at person@example.com"),
            ("shipping_hint", "123 Main St"),
            ("fulfillment_note", "call 555-123-4567 before delivery"),
            ("fulfillment_context", "call 5551234567 before delivery"),
            ("quote_context", "call +15551234567"),
            ("quote_context", "call (555)123-4567"),
            ("source_reference", "198.51.100.12"),
            ("source_reference", "gid://shopify/Customer/123"),
        ]
        for key, value in pii_cases:
            event = sample_counterpilot_events()["offer_opened"]
            event["pre_decision_context"][key] = value
            with self.subTest(key=key):
                with self.assertRaises(CounterpilotError):
                    validate_counterpilot_event(event)

        event = sample_counterpilot_events()["offer_opened"]
        event["pre_decision_context"]["final_sale_price"] = 760.0
        with self.assertRaises(CounterpilotError):
            validate_counterpilot_event(event)

        event = sample_counterpilot_events()["offer_opened"]
        event["available_actions"].append({"action": "manual_other"})
        with self.assertRaises(CounterpilotError):
            validate_counterpilot_event(event)

    def test_paid_mature_outcomes_require_component_reconciliation(self) -> None:
        event = sample_counterpilot_events()["outcome_matured"]
        validate_counterpilot_event(event)

        event = sample_counterpilot_events()["outcome_matured"]
        del event["outcome"]["actual_fees"]
        with self.assertRaises(CounterpilotError):
            validate_counterpilot_event(event)

        event = sample_counterpilot_events()["outcome_matured"]
        event["outcome"]["mature_contribution_margin"] = 999999.0
        with self.assertRaises(CounterpilotError):
            validate_counterpilot_event(event)

    def test_cli_template_ingest_and_audit_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            templates = Path(tmp) / "templates"
            data = Path(tmp) / "data"
            subprocess.run(
                [sys.executable, "-m", "behavior_lab", "counterpilot-template", "--output-dir", str(templates)],
                check=True,
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
            )
            events = [json.loads((templates / name).read_text(encoding="utf-8")) for name in ["merchant_consent.json", "offer_opened.json"]]
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, events)
            subprocess.run(
                [sys.executable, "-m", "behavior_lab", "counterpilot-ingest", "--input", str(source), "--data-dir", str(data)],
                check=True,
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
            )
            audited = subprocess.run(
                [sys.executable, "-m", "behavior_lab", "counterpilot-audit", "--data-dir", str(data), "--merchant-id", "merchant_demo_refurb_tech"],
                check=True,
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
            )
            payload = json.loads(audited.stdout)
            self.assertEqual(payload["counts"]["offers_opened"], 1)
            self.assertFalse(payload["automation_allowed"])


if __name__ == "__main__":
    unittest.main()
