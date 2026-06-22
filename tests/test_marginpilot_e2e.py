from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import _bootstrap  # noqa: F401,E402

from tools.marginpilot_e2e import run_development_store_e2e


class MarginPilotShopifyE2ETests(unittest.TestCase):
    def test_deterministic_shopify_commerce_loop_reaches_redacted_mature_margin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "MARGINPILOT_E2E_REPORT.md"
            report = run_development_store_e2e(data_dir=Path(tmp) / "data", report_path=report_path)

            self.assertEqual(report["events"]["mature_state"], "mature")
            self.assertEqual(report["merchant_inbox"]["offer_count_after_submit"], 1)
            self.assertEqual(report["merchant_inbox"]["first_offer_state"], "offer_submitted")
            self.assertTrue(report["idempotency_behavior"]["duplicate_order_created_replay"])
            self.assertTrue(report["out_of_order_behavior"]["return_close_pending_before_open"])
            self.assertEqual(report["out_of_order_behavior"]["state_after_reconciliation"], "return_closed")
            self.assertEqual(report["out_of_order_behavior"]["pending_after_reconciliation"], [])
            self.assertTrue(report["shopify_resource_linkage"]["checkout_link_available_to_delivery_flow"])
            self.assertEqual(report["shopify_resource_linkage"]["checkout_link_reported_value"], "operational_store_only")
            self.assertEqual(report["financial_components"]["mature_contribution_margin_minor"], 16166)
            self.assertFalse(report["model_recommendations_present"])

            transitions = [event["transition_to"] for event in report["state_transition_log"]]
            self.assertEqual(
                transitions,
                [
                    "offer_submitted",
                    "merchant_countered",
                    "buyer_accepted",
                    "checkout_created",
                    "order_created",
                    "paid",
                    "partially_refunded",
                    "return_opened",
                    "return_closed",
                    "mature",
                ],
            )
            self.assertEqual(len({event["event_id"] for event in report["state_transition_log"]}), len(report["state_transition_log"]))

            projection = report["research_projection"]
            self.assertEqual(projection["schema_version"], "marginpilot_research_export.v1")
            self.assertEqual(len(projection["rows"]), 1)
            self.assertTrue(all(report["pii_redaction"].values()))

            rendered = json.dumps(report, sort_keys=True)
            self.assertNotIn("buyer@example.com", rendered)
            self.assertNotIn("https://marginpilot-dev-store.myshopify.com", rendered)
            self.assertNotIn("gid://shopify", rendered)
            self.assertIn("operational_store", rendered)
            self.assertTrue(report_path.exists())


if __name__ == "__main__":
    unittest.main()
