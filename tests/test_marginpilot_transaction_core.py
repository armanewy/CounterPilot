from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import _bootstrap  # noqa: F401

from behavior_lab.marginpilot_core import (
    MERCHANT_SPECIFIC_MODEL_TRAINING,
    consent_grant,
    consent_revoke,
    event_append,
    local_commerce_fixture_events,
    research_export,
    run_local_commerce_fixture,
    transaction_create,
    transaction_inspect,
)
from behavior_lab.marginpilot_storage import ConsentRequiredError


class MarginPilotTransactionCoreTests(unittest.TestCase):
    def test_local_fixture_completes_mature_commerce_loop_without_research_pii(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_local_commerce_fixture(data_dir=tmp)
            rendered_export = json.dumps(result["research_export"], sort_keys=True)

            self.assertTrue(all(result["checks"].values()), result["checks"])
            self.assertEqual(result["transaction_snapshot"]["current_state"], "mature")
            self.assertTrue(result["duplicate_paid_result"]["idempotent_replay"])
            self.assertNotIn("buyer@example.com", rendered_export)
            self.assertNotIn("gid://shopify", rendered_export)
            self.assertNotIn("checkout.example.test", rendered_export)
            self.assertEqual(result["research_export"]["model_features"][0]["financial_mature_contribution_margin_minor"], 17166)

    def test_commands_create_append_inspect_consent_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "core"
            events = local_commerce_fixture_events(
                merchant_namespace="merchant_demo_refurb:store_demo_shopify",
                transaction_id="txn_cli_001",
            )
            event_paths = []
            for event in events:
                path = Path(tmp) / f"{event['event_id']}.json"
                path.write_text(json.dumps(event), encoding="utf-8")
                event_paths.append(path)

            created = transaction_create(data_dir=data_dir, input_path=event_paths[0])
            self.assertEqual(created["snapshot"]["current_state"], "offer_submitted")
            for path in event_paths[1:]:
                event_append(data_dir=data_dir, input_path=path)

            inspected = transaction_inspect(
                data_dir=data_dir,
                merchant_namespace="merchant_demo_refurb:store_demo_shopify",
                transaction_id="txn_cli_001",
            )
            self.assertEqual(inspected["current_state"], "mature")

            consent_grant(
                data_dir=data_dir,
                merchant_id="merchant_demo_refurb",
                store_id="store_demo_shopify",
                granted_at="2026-06-22T09:55:00+00:00",
            )
            fixture = run_local_commerce_fixture(data_dir=Path(tmp) / "fixture")
            self.assertTrue(fixture["checks"]["research_export_has_no_operational_pii"])

    def test_revocation_blocks_new_model_eligibility_but_not_existing_export_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_local_commerce_fixture(data_dir=tmp)
            existing_export = research_export(
                data_dir=tmp,
                merchant_id="merchant_demo_refurb",
                store_id="store_demo_shopify",
                purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
                as_of="2026-07-22T10:15:00+00:00",
            )
            consent_revoke(
                data_dir=tmp,
                merchant_id="merchant_demo_refurb",
                store_id="store_demo_shopify",
                purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
                revoked_at="2026-07-22T10:16:00+00:00",
            )

            with self.assertRaises(ConsentRequiredError):
                research_export(
                    data_dir=tmp,
                    merchant_id="merchant_demo_refurb",
                    store_id="store_demo_shopify",
                    purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
                    as_of="2026-07-22T10:17:00+00:00",
                )
            self.assertEqual(existing_export["model_features"][0]["financial_mature_contribution_margin_minor"], 17166)

    def test_cli_core_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "behavior_lab",
                    "marginpilot-run-local-fixture",
                    "--data-dir",
                    str(Path(tmp) / "fixture"),
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["checks"]["mature_state"])
            self.assertTrue(payload["checks"]["research_export_has_no_operational_pii"])


if __name__ == "__main__":
    unittest.main()
