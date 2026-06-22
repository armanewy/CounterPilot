from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import _bootstrap  # noqa: F401

from behavior_lab.marginpilot import (
    MarginPilotError,
    ingest_marginpilot_events,
    marginpilot_audit,
    marginpilot_inbox,
    sample_marginpilot_events,
    validate_marginpilot_event,
    write_marginpilot_templates,
)


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


class MarginPilotTests(unittest.TestCase):
    def test_templates_include_consent_and_month_one_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = write_marginpilot_templates(Path(tmp) / "templates")
            self.assertEqual(manifest["product_id"], "marginpilot_negotiated_commerce")
            self.assertIn("merchant_consent", manifest["events"])
            self.assertFalse(manifest["data_rights"]["cross_merchant_pooling_default"])
            self.assertIn("offer and quote event capture", manifest["month_1_scope"])

    def test_ingest_inbox_accounting_and_audit_are_consent_gated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_marginpilot_events()
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, [events["merchant_consent"], events["offer_opened"]])
            result = ingest_marginpilot_events(source, data_dir=Path(tmp) / "data")
            self.assertEqual(result.imported, 2)

            inbox = marginpilot_inbox(Path(tmp) / "data", merchant_id="merchant_demo_refurb_tech")
            self.assertEqual(inbox["open_offer_count"], 1)
            self.assertFalse(inbox["executes_seller_actions"])
            economics = inbox["open_offers"][0]["economics"]
            accept = next(row for row in economics if row["action"] == "accept")
            self.assertEqual(accept["mature_margin_if_sold"], 114.82)
            self.assertFalse(accept["violates_merchant_floor"])
            self.assertTrue(inbox["open_offers"][0]["merchant_specific_learning_authorized"])

            audit = marginpilot_audit(Path(tmp) / "data", merchant_id="merchant_demo_refurb_tech")
            self.assertFalse(audit["profit_optimization_gate"]["passed"])
            self.assertTrue(audit["profit_optimization_gate"]["checks"]["merchant_specific_learning_consent"])
            self.assertFalse(audit["automation_allowed"])
            self.assertEqual(audit["model_training"], "not_run")

    def test_free_shipping_counter_keeps_merchant_shipping_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_marginpilot_events()
            events["offer_opened"]["available_actions"].append({"action": "free_shipping_counter", "amount": 760.0})
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, [events["merchant_consent"], events["offer_opened"]])
            ingest_marginpilot_events(source, data_dir=Path(tmp) / "data")

            inbox = marginpilot_inbox(Path(tmp) / "data", merchant_id="merchant_demo_refurb_tech")
            economics = inbox["open_offers"][0]["economics"]
            free_shipping = next(row for row in economics if row["action"] == "free_shipping_counter")

            self.assertEqual(free_shipping["mature_margin_if_sold"], 153.66)
            self.assertNotEqual(free_shipping["mature_margin_if_sold"], 187.66)

    def test_audit_reports_mature_margin_without_training(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_marginpilot_events()
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, list(events.values()))
            ingest_marginpilot_events(source, data_dir=Path(tmp) / "data")

            audit = marginpilot_audit(Path(tmp) / "data", merchant_id="merchant_demo_refurb_tech")

            self.assertEqual(audit["counts"]["mature_paid_outcomes"], 1)
            self.assertEqual(audit["mature_contribution_margin"]["total"], 171.66)
            self.assertFalse(audit["data_rights"]["cross_merchant_pooling_authorized"])
            self.assertEqual(audit["current_stage"], "transaction_surface")

    def test_inbox_scopes_consent_and_decisions_by_merchant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_marginpilot_events()
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
            ingest_marginpilot_events(source, data_dir=Path(tmp) / "data")

            inbox = marginpilot_inbox(Path(tmp) / "data")

            self.assertEqual(inbox["open_offer_count"], 1)
            self.assertEqual(inbox["open_offers"][0]["merchant_id"], "merchant_without_consent")
            self.assertFalse(inbox["open_offers"][0]["merchant_specific_learning_authorized"])

    def test_audit_blocks_cross_merchant_pooling_and_bad_threads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = sample_marginpilot_events()
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
            ingest_marginpilot_events(source, data_dir=Path(tmp) / "data")

            aggregate = marginpilot_audit(Path(tmp) / "data")
            merchant_a = marginpilot_audit(Path(tmp) / "data", merchant_id="merchant_demo_refurb_tech")

            self.assertFalse(aggregate["profit_optimization_gate"]["checks"]["single_merchant_namespace"])
            self.assertFalse(aggregate["data_rights"]["merchant_specific_learning_authorized"])
            self.assertFalse(merchant_a["profit_optimization_gate"]["checks"]["event_thread_integrity"])
            self.assertIn("unavailable action", merchant_a["profit_optimization_gate"]["event_thread_integrity"]["errors"][0])

    def test_rejects_customer_pii_and_post_decision_context(self) -> None:
        validate_marginpilot_event(sample_marginpilot_events()["offer_opened"])

        event = sample_marginpilot_events()["offer_opened"]
        event["pre_decision_context"]["buyer_email"] = "person@example.com"
        with self.assertRaises(MarginPilotError):
            validate_marginpilot_event(event)

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
            event = sample_marginpilot_events()["offer_opened"]
            event["pre_decision_context"][key] = value
            with self.subTest(key=key):
                with self.assertRaises(MarginPilotError):
                    validate_marginpilot_event(event)

        event = sample_marginpilot_events()["offer_opened"]
        event["pre_decision_context"]["final_sale_price"] = 760.0
        with self.assertRaises(MarginPilotError):
            validate_marginpilot_event(event)

        event = sample_marginpilot_events()["offer_opened"]
        event["available_actions"].append({"action": "manual_other"})
        with self.assertRaises(MarginPilotError):
            validate_marginpilot_event(event)

    def test_paid_mature_outcomes_require_component_reconciliation(self) -> None:
        event = sample_marginpilot_events()["outcome_matured"]
        validate_marginpilot_event(event)

        event = sample_marginpilot_events()["outcome_matured"]
        del event["outcome"]["actual_fees"]
        with self.assertRaises(MarginPilotError):
            validate_marginpilot_event(event)

        event = sample_marginpilot_events()["outcome_matured"]
        event["outcome"]["mature_contribution_margin"] = 999999.0
        with self.assertRaises(MarginPilotError):
            validate_marginpilot_event(event)

    def test_cli_template_ingest_and_audit_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            templates = Path(tmp) / "templates"
            data = Path(tmp) / "data"
            subprocess.run(
                [sys.executable, "-m", "behavior_lab", "marginpilot-template", "--output-dir", str(templates)],
                check=True,
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
            )
            events = [json.loads((templates / name).read_text(encoding="utf-8")) for name in ["merchant_consent.json", "offer_opened.json"]]
            source = Path(tmp) / "events.jsonl"
            _write_jsonl(source, events)
            subprocess.run(
                [sys.executable, "-m", "behavior_lab", "marginpilot-ingest", "--input", str(source), "--data-dir", str(data)],
                check=True,
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
            )
            audited = subprocess.run(
                [sys.executable, "-m", "behavior_lab", "marginpilot-audit", "--data-dir", str(data), "--merchant-id", "merchant_demo_refurb_tech"],
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
