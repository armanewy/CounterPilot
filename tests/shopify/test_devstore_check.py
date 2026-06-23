from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from integrations.shopify.devstore_check import (
    counterpilot_devstore_check,
    write_redacted_devstore_proof_artifact,
)


def _env(tmp: str) -> dict[str, str]:
    return {
        "COUNTERPILOT_DATA_DIR": tmp,
        "COUNTERPILOT_MERCHANT_ID": "merchant_demo_refurb",
        "COUNTERPILOT_SHOPIFY_ACCESS_TOKEN": "shpat_development_secret_token",
        "COUNTERPILOT_SHOPIFY_APP_URL": "https://counterpilot-app.example.test",
        "COUNTERPILOT_SHOPIFY_PROVIDER_MODE": "real",
        "COUNTERPILOT_SHOPIFY_SCOPES": "read_orders,read_products,write_draft_orders",
        "COUNTERPILOT_SHOPIFY_STORE_DOMAIN": "counterpilot-dev-store.myshopify.com",
        "COUNTERPILOT_SHOPIFY_STORE_MODE": "development",
        "COUNTERPILOT_SHOPIFY_WEBHOOK_SECRET": "dev_webhook_secret",
        "COUNTERPILOT_SHOPIFY_WEBHOOK_URL": "https://counterpilot-app.example.test/webhooks/shopify",
        "COUNTERPILOT_STORE_ID": "store_demo_shopify",
    }


class CounterpilotDevStoreCheckTests(unittest.TestCase):
    def test_devstore_check_redacts_token_and_validates_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = counterpilot_devstore_check(
                env=_env(tmp),
                network_probe=lambda domain: (True, f"reachable:{domain}"),
            )

            rendered = json.dumps(result, sort_keys=True)
            self.assertTrue(result["ok"])
            self.assertFalse(result["mutations_performed"])
            self.assertFalse(result["token_printed"])
            self.assertNotIn("shpat_development_secret_token", rendered)
            self.assertIn("store_domain_hash", result)

    def test_devstore_check_rejects_fake_provider_and_missing_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            env["COUNTERPILOT_SHOPIFY_PROVIDER_MODE"] = "fake"
            env["COUNTERPILOT_SHOPIFY_SCOPES"] = "read_orders"

            result = counterpilot_devstore_check(env=env, network_probe=lambda domain: (True, "reachable"))

            self.assertFalse(result["ok"])
            failed = {check["check"]: check for check in result["checks"] if not check["passed"]}
            self.assertIn("provider_mode_real", failed)
            self.assertIn("required_scopes", failed)

    def test_redacted_proof_artifact_hashes_shopify_ids_and_rejects_pii(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "counterpilot_dev_store_proof.json"
            artifact = write_redacted_devstore_proof_artifact(
                {
                    "app_version": "0.1-dev",
                    "transaction_id": "cp_txn_abc",
                    "event_ids": ["offer", "paid", "mature"],
                    "state_transition_sequence": ["offer_submitted", "paid", "mature"],
                    "shopify_resource_ids": {"order_gid": "gid://shopify/Order/123"},
                    "final_mature_margin_components": {"mature_contribution_margin_minor": 16166},
                    "report": {"schema_version": "counterpilot_merchant_report.v1"},
                    "research_export": {"schema_version": "counterpilot_research_export.v1", "rows": []},
                    "pii_scan": {"passed": True},
                    "manual_steps_completed": {"theme_block_enabled": True},
                    "skipped_steps": [{"step": "refund", "reason": "simulated through dev flow"}],
                },
                output_path=output,
                git_commit="abc1234",
                timestamp="2026-06-23T10:00:00+00:00",
            )

            rendered = output.read_text(encoding="utf-8")
            self.assertEqual(artifact["store_mode"], "development")
            self.assertFalse(artifact["production_evidence"])
            self.assertNotIn("gid://shopify", rendered)
            self.assertIn("order_gid", artifact["shopify_resource_hashes"])

            with self.assertRaises(ValueError):
                write_redacted_devstore_proof_artifact({"email": "buyer@example.com"}, output_path=Path(tmp) / "bad.json")


if __name__ == "__main__":
    unittest.main()
