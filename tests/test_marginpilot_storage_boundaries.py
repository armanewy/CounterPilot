from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import _bootstrap  # noqa: F401

from behavior_lab.marginpilot_storage import (
    CROSS_MERCHANT_TRAINING,
    MERCHANT_SPECIFIC_MODEL_TRAINING,
    BoundaryViolation,
    ConsentLedger,
    ConsentRecord,
    ConsentRequiredError,
    EphemeralMappingLayer,
    InMemoryEncryptedAtRestAdapter,
    OperationalTransactionRecord,
    OperationalTransactionStore,
    PIIScanError,
    PIIScanner,
    ResearchEventRecord,
    ResearchEventStore,
    build_model_feature_matrix,
    production_artifact_manifest,
)
from behavior_lab.marginpilot_storage.stores import OPERATIONAL_COLLECTION


MERCHANT = "merchant_demo_refurb"
STORE = "store_demo_shopify"
GRANTED_AT = "2026-06-22T10:00:00+00:00"
TRAINING_AS_OF = "2026-06-22T10:05:00+00:00"
REVOKED_AT = "2026-06-22T10:10:00+00:00"


def _grant(
    consent: ConsentLedger,
    *,
    merchant_id: str = MERCHANT,
    store_id: str = STORE,
    purposes: tuple[str, ...] = (MERCHANT_SPECIFIC_MODEL_TRAINING,),
    prohibited: tuple[str, ...] = (CROSS_MERCHANT_TRAINING,),
) -> None:
    consent.append(
        ConsentRecord(
            merchant_id=merchant_id,
            store_id=store_id,
            consent_policy_version="marginpilot-consent-v1",
            policy_hash="policy_hash_v1",
            granted_purposes=purposes,
            prohibited_purposes=prohibited,
            granted_at=GRANTED_AT,
            provenance={"source": "unit_test"},
        )
    )


def _operational_record(
    *,
    merchant_id: str = MERCHANT,
    store_id: str = STORE,
    transaction_id: str = "shopify_order_1001",
    email: str = "buyer@example.com",
) -> OperationalTransactionRecord:
    return OperationalTransactionRecord(
        merchant_id=merchant_id,
        store_id=store_id,
        operational_transaction_id=transaction_id,
        shopify_resource_ids={
            "checkout_gid": f"gid://shopify/Checkout/{transaction_id}",
            "customer_gid": f"gid://shopify/Customer/{transaction_id}",
            "order_gid": f"gid://shopify/Order/{transaction_id}",
            "payment_gid": f"gid://shopify/Payment/{transaction_id}",
        },
        contact_delivery_reference=f"contact_delivery_ref_{transaction_id}",
        checkout_url_reference=f"https://checkout.example.test/{transaction_id}?email={email}",
        fulfillment_state="unfulfilled",
        payment_state="authorized",
        retention_policy="delete_customer_data_after_return_window",
        retention_expires_at="2026-07-22T10:00:00+00:00",
        operational_customer_data={
            "email": email,
            "phone": "555-123-4567",
            "shipping_address": "123 Main St",
        },
        provenance={"source": "shopify_adapter_fixture"},
    )


def _append_research_event(
    research: ResearchEventStore,
    consent: ConsentLedger,
    mapping: EphemeralMappingLayer,
    operational: OperationalTransactionRecord,
    *,
    event_id: str = "research_event_001",
) -> ResearchEventRecord:
    evidence = consent.latest_evidence(
        merchant_id=operational.merchant_id,
        store_id=operational.store_id,
        purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
        require_active=False,
        as_of=TRAINING_AS_OF,
    )
    event = ResearchEventRecord.from_operational(
        operational,
        mapping,
        event_id=event_id,
        occurred_at=TRAINING_AS_OF,
        offer_context={
            "asking_price": 150.0,
            "buyer_offer_amount": 120.0,
            "category": "refurbished technology",
            "condition": "refurbished",
            "surface": "cart_offer",
        },
        decisions={"amount": 130.0, "selected_action": "counter_at_amount"},
        outcomes={"buyer_paid": True, "return_window_matured": True, "returned": False},
        financial_components={
            "cost_basis": 50.0,
            "mature_contribution_margin": 70.8,
            "payment_fee": 4.2,
            "shipping_cost": 5.0,
        },
        consent_policy_version=evidence.get("consent_policy_version"),
        consent_policy_hash=evidence.get("policy_hash"),
    )
    research.append(event)
    return event


class MarginPilotStorageBoundaryTests(unittest.TestCase):
    def test_operational_data_is_encrypted_and_research_survives_pii_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            consent = ConsentLedger(Path(tmp) / "consent.jsonl")
            _grant(consent)
            adapter = InMemoryEncryptedAtRestAdapter(key=b"marginpilot-test-encryption-key")
            operational_store = OperationalTransactionStore(adapter)
            operational = _operational_record()
            storage_id = operational_store.put(operational)

            ciphertext = adapter.raw_ciphertext(OPERATIONAL_COLLECTION, storage_id)
            self.assertNotIn(b"buyer@example.com", ciphertext)
            self.assertNotIn(b"gid://shopify/Customer", ciphertext)
            self.assertNotIn(b"checkout.example.test", ciphertext)

            mapping = EphemeralMappingLayer(secret=b"marginpilot-test-mapping-secret")
            email_pseudonym = mapping.transform(
                "buyer@example.com",
                merchant_id=MERCHANT,
                store_id=STORE,
                namespace="buyer",
            )
            email_sha256 = hashlib.sha256(b"buyer@example.com").hexdigest()
            self.assertNotEqual(email_pseudonym.pseudonymous_id, email_sha256)
            self.assertNotIn(email_sha256[:16], email_pseudonym.pseudonymous_id)

            research = ResearchEventStore(Path(tmp) / "research.jsonl", consent_ledger=consent)
            _append_research_event(research, consent, mapping, operational)
            research_payload = json.dumps(research.events(), sort_keys=True)
            self.assertNotIn("buyer@example.com", research_payload)
            self.assertNotIn("gid://shopify", research_payload)
            self.assertNotIn("checkout.example.test", research_payload)

            dataset = research.training_dataset(
                merchant_id=MERCHANT,
                store_id=STORE,
                purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
                as_of=TRAINING_AS_OF,
            )
            artifact = production_artifact_manifest(
                artifact_id="marginpilot_model_artifact_001",
                model_id="shadow_margin_model",
                dataset=dataset,
                created_at=TRAINING_AS_OF,
            )
            self.assertTrue(artifact["dataset_lineage"]["event_hashes"])
            self.assertTrue(artifact["consent_lineage"])

            deletion = operational_store.delete_customer_data(
                merchant_id=MERCHANT,
                store_id=STORE,
                operational_transaction_id="shopify_order_1001",
                deleted_at=REVOKED_AT,
            )
            self.assertTrue(deletion["deleted"])
            self.assertIsNone(
                operational_store.get(
                    merchant_id=MERCHANT,
                    store_id=STORE,
                    operational_transaction_id="shopify_order_1001",
                )
            )
            mapping.delete_rotation("rotation_001")
            self.assertEqual(mapping.active_count, 0)

            after_deletion = research.training_dataset(
                merchant_id=MERCHANT,
                store_id=STORE,
                purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
                as_of=TRAINING_AS_OF,
            )
            self.assertEqual(len(after_deletion.rows), 1)
            self.assertEqual(build_model_feature_matrix(after_deletion)[0]["category"], "refurbished technology")

    def test_purpose_specific_consent_and_revocation_gate_training_without_rewriting_ledgers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            consent = ConsentLedger(Path(tmp) / "consent.jsonl")
            mapping = EphemeralMappingLayer(secret=b"marginpilot-test-mapping-secret")
            research = ResearchEventStore(Path(tmp) / "research.jsonl", consent_ledger=consent)
            operational = _operational_record()
            _append_research_event(research, consent, mapping, operational)

            with self.assertRaises(ConsentRequiredError):
                research.training_dataset(
                    merchant_id=MERCHANT,
                    store_id=STORE,
                    purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
                    as_of=TRAINING_AS_OF,
                )

            _grant(consent)
            before_revocation = research.training_dataset(
                merchant_id=MERCHANT,
                store_id=STORE,
                purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
                as_of=TRAINING_AS_OF,
            )
            event_hash_before = research.events()[0]

            consent.revoke(
                merchant_id=MERCHANT,
                store_id=STORE,
                purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
                revoked_at=REVOKED_AT,
                provenance={"source": "merchant_revocation"},
            )
            with self.assertRaises(ConsentRequiredError):
                research.training_dataset(
                    merchant_id=MERCHANT,
                    store_id=STORE,
                    purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
                    as_of="2026-06-22T10:11:00+00:00",
                )

            self.assertEqual(len(before_revocation.rows), 1)
            self.assertEqual(research.events()[0], event_hash_before)
            self.assertEqual(len(consent.records()), 2)

    def test_cross_merchant_training_defaults_to_prohibited_until_explicit_cross_consent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            consent = ConsentLedger(Path(tmp) / "consent.jsonl")
            mapping = EphemeralMappingLayer(secret=b"marginpilot-test-mapping-secret")
            research = ResearchEventStore(Path(tmp) / "research.jsonl", consent_ledger=consent)
            merchants = [("merchant_a", "store_a", "order_a"), ("merchant_b", "store_b", "order_b")]
            for merchant_id, store_id, order_id in merchants:
                _grant(consent, merchant_id=merchant_id, store_id=store_id)
                _append_research_event(
                    research,
                    consent,
                    mapping,
                    _operational_record(merchant_id=merchant_id, store_id=store_id, transaction_id=order_id),
                    event_id=f"research_{order_id}",
                )

            with self.assertRaises(ConsentRequiredError):
                research.training_dataset(
                    purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
                    as_of=TRAINING_AS_OF,
                )
            with self.assertRaises(ConsentRequiredError):
                research.training_dataset(
                    purpose=CROSS_MERCHANT_TRAINING,
                    cross_merchant=True,
                    as_of=TRAINING_AS_OF,
                )

            for merchant_id, store_id, _ in merchants:
                _grant(
                    consent,
                    merchant_id=merchant_id,
                    store_id=store_id,
                    purposes=(CROSS_MERCHANT_TRAINING,),
                    prohibited=(),
                )
            dataset = research.training_dataset(
                purpose=CROSS_MERCHANT_TRAINING,
                cross_merchant=True,
                as_of=TRAINING_AS_OF,
            )
            self.assertEqual(len(dataset.rows), 2)

    def test_scanner_rejects_pii_in_keys_values_nested_urls_metadata_and_exceptions(self) -> None:
        scanner = PIIScanner()
        pii_payloads = [
            {"customer": {"id": "customer_123"}},
            {"offer_context": {"contact_email": "opaque-ref"}},
            {"metadata": {"nested": [{"message": "email buyer@example.com before delivery"}]}},
            {"checkout_reference": "https://checkout.example.test/checkouts/abc"},
            {"metadata": {"exception": ValueError("failed for buyer@example.com")}},
            {"metadata": {"shopify_resource_ids": {"order": "gid://shopify/Order/123"}}},
        ]
        for payload in pii_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(PIIScanError) as raised:
                    scanner.scan(payload, label="test payload")
                self.assertNotIn("buyer@example.com", str(raised.exception))
                self.assertNotIn("gid://shopify", str(raised.exception))

        adapter = InMemoryEncryptedAtRestAdapter(key=b"marginpilot-test-encryption-key")
        with self.assertRaises(PIIScanError) as raised:
            adapter.write("collection", "record", b"{}", metadata={"customer_email": "buyer@example.com"})
        self.assertNotIn("buyer@example.com", str(raised.exception))

    def test_model_feature_builder_rejects_operational_store_and_uses_research_features_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            consent = ConsentLedger(Path(tmp) / "consent.jsonl")
            _grant(consent)
            adapter = InMemoryEncryptedAtRestAdapter(key=b"marginpilot-test-encryption-key")
            operational_store = OperationalTransactionStore(adapter)
            operational = _operational_record()
            operational_store.put(operational)
            mapping = EphemeralMappingLayer(secret=b"marginpilot-test-mapping-secret")
            research = ResearchEventStore(Path(tmp) / "research.jsonl", consent_ledger=consent)
            _append_research_event(research, consent, mapping, operational)

            with self.assertRaises(BoundaryViolation):
                build_model_feature_matrix(operational_store)  # type: ignore[arg-type]

            dataset = research.training_dataset(
                merchant_id=MERCHANT,
                store_id=STORE,
                purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
                as_of=TRAINING_AS_OF,
            )
            features = build_model_feature_matrix(dataset)
            self.assertEqual(features, [dataset.rows[0]["features"]])
            self.assertNotIn("pseudonymous_buyer_id", features[0])
            self.assertNotIn("shopify_resource_ids", json.dumps(features))


if __name__ == "__main__":
    unittest.main()
