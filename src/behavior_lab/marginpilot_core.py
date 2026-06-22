from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from behavior_lab.core import stable_hash, utc_now
from behavior_lab.marginpilot import DEFAULT_DATA_DIR
from behavior_lab.marginpilot_state import (
    MARGINPILOT_STATE_SCHEMA_VERSION,
    TransactionStateMachine,
    money,
)
from behavior_lab.marginpilot_storage import (
    CROSS_MERCHANT_TRAINING,
    MERCHANT_SPECIFIC_MODEL_TRAINING,
    ConsentLedger,
    ConsentRecord,
    EphemeralMappingLayer,
    LocalFileEncryptedAtRestAdapter,
    OperationalTransactionRecord,
    OperationalTransactionStore,
    ResearchEventRecord,
    ResearchEventStore,
    build_model_feature_matrix,
)


DEFAULT_CORE_DATA_DIR = DEFAULT_DATA_DIR / "transaction_core"
DEFAULT_CONSENT_VERSION = "marginpilot-ml-consent-v1"
DEFAULT_PURPOSES = (
    "merchant_specific_shadow_recommendations",
    MERCHANT_SPECIFIC_MODEL_TRAINING,
    "merchant_specific_policy_evaluation",
)


def transaction_create(
    *,
    data_dir: str | Path = DEFAULT_CORE_DATA_DIR,
    input_path: str | Path | None = None,
) -> dict[str, Any]:
    event = _load_json(input_path) if input_path is not None else sample_offer_submitted_event()
    result = TransactionStateMachine(data_dir).append_event(event)
    return {"result": asdict(result), "snapshot": transaction_inspect(data_dir=data_dir, merchant_namespace=event["merchant_namespace"], transaction_id=event["transaction_id"])}


def event_append(
    *,
    data_dir: str | Path = DEFAULT_CORE_DATA_DIR,
    input_path: str | Path,
) -> dict[str, Any]:
    event = _load_json(input_path)
    result = TransactionStateMachine(data_dir).append_event(event)
    return {"result": asdict(result), "snapshot": transaction_inspect(data_dir=data_dir, merchant_namespace=event["merchant_namespace"], transaction_id=event["transaction_id"])}


def transaction_inspect(
    *,
    data_dir: str | Path = DEFAULT_CORE_DATA_DIR,
    merchant_namespace: str,
    transaction_id: str,
) -> dict[str, Any]:
    return TransactionStateMachine(data_dir).inspect(merchant_namespace, transaction_id)


def consent_grant(
    *,
    data_dir: str | Path = DEFAULT_CORE_DATA_DIR,
    merchant_id: str,
    store_id: str,
    purposes: list[str] | tuple[str, ...] | None = None,
    consent_version: str = DEFAULT_CONSENT_VERSION,
    policy_hash: str | None = None,
    granted_at: str | None = None,
    cross_merchant_training: bool = False,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    granted_purposes = tuple(purposes or DEFAULT_PURPOSES)
    prohibited = () if cross_merchant_training else (CROSS_MERCHANT_TRAINING,)
    policy_body = {"consent_version": consent_version, "purposes": granted_purposes, "cross_merchant_training": cross_merchant_training}
    record = ConsentRecord(
        merchant_id=merchant_id,
        store_id=store_id,
        consent_policy_version=consent_version,
        policy_hash=policy_hash or stable_hash(policy_body),
        granted_purposes=granted_purposes,
        prohibited_purposes=prohibited,
        granted_at=granted_at or utc_now(),
        provenance=provenance or {"source": "marginpilot_consent_grant"},
    )
    appended = _consent_ledger(data_dir).append(record)
    return {"record_id": appended["record_id"], "record_hash": appended["record_hash"], "payload": appended["payload"]}


def consent_revoke(
    *,
    data_dir: str | Path = DEFAULT_CORE_DATA_DIR,
    merchant_id: str,
    store_id: str,
    purpose: str,
    revoked_at: str | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    appended = _consent_ledger(data_dir).revoke(
        merchant_id=merchant_id,
        store_id=store_id,
        purpose=purpose,
        revoked_at=revoked_at or utc_now(),
        provenance=provenance or {"source": "marginpilot_consent_revoke"},
    )
    return {"record_id": appended["record_id"], "record_hash": appended["record_hash"], "payload": appended["payload"]}


def research_export(
    *,
    data_dir: str | Path = DEFAULT_CORE_DATA_DIR,
    merchant_id: str | None = None,
    store_id: str | None = None,
    purpose: str = MERCHANT_SPECIFIC_MODEL_TRAINING,
    as_of: str | None = None,
) -> dict[str, Any]:
    dataset = _research_store(data_dir).training_dataset(
        merchant_id=merchant_id,
        store_id=store_id,
        purpose=purpose,
        as_of=as_of,
    )
    return {
        "schema_version": "marginpilot_research_export.v1",
        "purpose": dataset.purpose,
        "rows": list(dataset.rows),
        "dataset_lineage": dataset.dataset_lineage,
        "consent_lineage": list(dataset.consent_lineage),
        "model_features": build_model_feature_matrix(dataset),
    }


def run_local_commerce_fixture(*, data_dir: str | Path = DEFAULT_CORE_DATA_DIR) -> dict[str, Any]:
    data_root = Path(data_dir)
    merchant_id = "merchant_demo_refurb"
    store_id = "store_demo_shopify"
    transaction_id = "txn_marginpilot_loop_001"
    merchant_namespace = f"{merchant_id}:{store_id}"
    machine = TransactionStateMachine(data_root)

    events = local_commerce_fixture_events(merchant_namespace=merchant_namespace, transaction_id=transaction_id)
    append_results = [asdict(machine.append_event(event)) for event in events]
    duplicate_paid = asdict(machine.append_event(events[5]))
    snapshot = machine.inspect(merchant_namespace, transaction_id)

    operational_store = _operational_store(data_root)
    operational = OperationalTransactionRecord(
        merchant_id=merchant_id,
        store_id=store_id,
        operational_transaction_id="shopify_order_1001",
        shopify_resource_ids={
            "checkout_gid": "gid://shopify/Checkout/1001",
            "customer_gid": "gid://shopify/Customer/9001",
            "order_gid": "gid://shopify/Order/1001",
            "payment_gid": "gid://shopify/Payment/2001",
        },
        contact_delivery_reference="contact_delivery_ref_1001",
        checkout_url_reference="https://checkout.example.test/invoice/1001",
        fulfillment_state="fulfilled",
        payment_state="paid",
        retention_policy="delete_customer_data_after_return_window",
        retention_expires_at="2026-07-22T10:14:00+00:00",
        operational_customer_data={
            "email": "buyer@example.com",
            "phone": "555-123-4567",
            "shipping_address": "123 Main St",
        },
        provenance={"source": "local_marginpilot_fixture"},
    )
    operational_storage_id = operational_store.put(operational)

    consent = consent_grant(
        data_dir=data_root,
        merchant_id=merchant_id,
        store_id=store_id,
        granted_at="2026-06-22T09:55:00+00:00",
        provenance={"source": "local_marginpilot_fixture"},
    )
    research = _append_fixture_research_event(data_root, operational)
    export = research_export(
        data_dir=data_root,
        merchant_id=merchant_id,
        store_id=store_id,
        purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
        as_of="2026-07-22T10:15:00+00:00",
    )
    revoke = consent_revoke(
        data_dir=data_root,
        merchant_id=merchant_id,
        store_id=store_id,
        purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
        revoked_at="2026-07-22T10:16:00+00:00",
        provenance={"source": "local_marginpilot_fixture"},
    )
    revoked_blocks_new_training = False
    try:
        research_export(
            data_dir=data_root,
            merchant_id=merchant_id,
            store_id=store_id,
            purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
            as_of="2026-07-22T10:17:00+00:00",
        )
    except Exception:
        revoked_blocks_new_training = True
    return {
        "schema_version": "marginpilot_local_commerce_fixture.v1",
        "transaction_snapshot": snapshot,
        "append_results": append_results,
        "duplicate_paid_result": duplicate_paid,
        "operational_storage_id": operational_storage_id,
        "consent_record": consent,
        "research_record": research,
        "research_export": export,
        "revoke_record": revoke,
        "revoked_blocks_new_training": revoked_blocks_new_training,
        "checks": {
            "mature_state": snapshot["current_state"] == "mature",
            "duplicate_events_do_not_change_totals": duplicate_paid["idempotent_replay"] and snapshot["event_count"] == len(events),
            "research_export_has_no_operational_pii": "buyer@example.com" not in json.dumps(export, sort_keys=True)
            and "gid://shopify" not in json.dumps(export, sort_keys=True)
            and "checkout.example.test" not in json.dumps(export, sort_keys=True),
            "consent_is_purpose_specific": export["consent_lineage"][0]["purpose"] == MERCHANT_SPECIFIC_MODEL_TRAINING,
            "revocation_blocks_new_model_eligibility": revoked_blocks_new_training,
            "mature_margin_reconciles_exactly": snapshot["mature_outcome"]["mature_contribution_margin"] == money(17166),
        },
    }


def sample_offer_submitted_event() -> dict[str, Any]:
    return local_commerce_fixture_events(merchant_namespace="merchant_demo_refurb:store_demo_shopify", transaction_id="txn_marginpilot_loop_001")[0]


def local_commerce_fixture_events(*, merchant_namespace: str, transaction_id: str) -> list[dict[str, Any]]:
    def event(transition_to: str, event_id: str, occurred_at: str, **extra: Any) -> dict[str, Any]:
        body = {
            "schema_version": MARGINPILOT_STATE_SCHEMA_VERSION,
            "event_id": event_id,
            "merchant_namespace": merchant_namespace,
            "transaction_id": transaction_id,
            "occurred_at": occurred_at,
            "received_at": extra.pop("received_at", occurred_at),
            "source": extra.pop("source", "local_fixture"),
            "idempotency_key": f"fixture_{event_id}",
            "transition_to": transition_to,
            "currency": "USD",
        }
        body.update(extra)
        return body

    action_fields = {
        "available_actions": [{"action": "counter"}, {"action": "accept"}, {"action": "decline"}, {"action": "create_checkout"}],
        "recommendation": {"system_mode": "manual_only", "recommendation_id": None},
        "merchant_decision": {"action": "counter", "actor": "merchant"},
        "executed_action": {"action": "counter"},
    }
    checkout_action_fields = {
        "available_actions": [{"action": "create_checkout"}, {"action": "cancel"}],
        "recommendation": {"system_mode": "manual_only", "recommendation_id": None},
        "merchant_decision": {"action": "create_checkout", "actor": "merchant"},
        "executed_action": {"action": "create_checkout"},
    }
    return [
        event(
            "offer_submitted",
            "fixture_offer_submitted",
            "2026-06-22T10:00:00+00:00",
            line_items=[{"sku": "refurb-pc-i7", "quantity": 1, "unit_price": money(90000)}],
            economics={"buyer_offer": money(72000), "shipping_cost": money(3400), "cost_basis": money(52000)},
        ),
        event(
            "merchant_countered",
            "fixture_merchant_countered",
            "2026-06-22T10:05:00+00:00",
            discounts=[{"type": "shipping", "amount": money(3400)}],
            economics={"counter_amount": money(76000), "shipping_cost": money(3400), "cost_basis": money(52000)},
            **action_fields,
        ),
        event("buyer_accepted", "fixture_buyer_accepted", "2026-06-22T10:10:00+00:00", source="buyer_surface"),
        event("checkout_created", "fixture_checkout_created", "2026-06-22T10:11:00+00:00", checkout_reference={"kind": "draft_order_invoice"}, **checkout_action_fields),
        event("order_created", "fixture_order_created", "2026-06-22T10:12:00+00:00", source="shopify_webhook"),
        event("paid", "fixture_paid", "2026-06-22T10:14:00+00:00", source="shopify_webhook"),
        event(
            "mature",
            "fixture_mature",
            "2026-07-22T10:14:00+00:00",
            mature_outcome={
                "payment_resolution": "paid",
                "refund_return_maturity_date": "2026-07-22T10:14:00+00:00",
                "reconciled_fees": money(2234),
                "reconciled_fulfillment_costs": money(4600),
                "mature_contribution_margin": money(17166),
            },
        ),
    ]


def _append_fixture_research_event(data_dir: str | Path, operational: OperationalTransactionRecord) -> dict[str, Any]:
    data_root = Path(data_dir)
    consent_ledger = _consent_ledger(data_root)
    evidence = consent_ledger.latest_evidence(
        merchant_id=operational.merchant_id,
        store_id=operational.store_id,
        purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
        as_of="2026-07-22T10:15:00+00:00",
    )
    mapping = EphemeralMappingLayer(secret=_mapping_secret(data_root))
    event = ResearchEventRecord.from_operational(
        operational,
        mapping,
        event_id="research_fixture_marginpilot_loop_001",
        occurred_at="2026-07-22T10:15:00+00:00",
        offer_context={
            "category": "refurbished technology",
            "condition": "refurbished",
            "surface": "product_page_offer",
            "asking_price_minor": 90000,
            "buyer_offer_amount_minor": 72000,
            "counter_amount_minor": 76000,
            "inventory_age_days": 31,
        },
        decisions={"selected_action": "counter_at_amount", "amount_minor": 76000},
        outcomes={"buyer_paid": True, "return_window_matured": True, "returned": False},
        financial_components={
            "cost_basis_minor": 52000,
            "reconciled_fees_minor": 2234,
            "reconciled_fulfillment_costs_minor": 4600,
            "mature_contribution_margin_minor": 17166,
        },
        consent_policy_version=evidence["consent_policy_version"],
        consent_policy_hash=evidence["policy_hash"],
        provenance={"source": "local_marginpilot_fixture"},
    )
    record = _research_store(data_root).append(event)
    return {"record_id": record["record_id"], "record_hash": record["record_hash"], "payload": record["payload"]}


def _load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")
    return payload


def _consent_ledger(data_dir: str | Path) -> ConsentLedger:
    return ConsentLedger(Path(data_dir) / "consent.jsonl")


def _research_store(data_dir: str | Path) -> ResearchEventStore:
    return ResearchEventStore(Path(data_dir) / "research_events.jsonl", consent_ledger=_consent_ledger(data_dir))


def _operational_store(data_dir: str | Path) -> OperationalTransactionStore:
    return OperationalTransactionStore(LocalFileEncryptedAtRestAdapter(Path(data_dir) / "operational_encrypted"))


def _mapping_secret(data_dir: str | Path) -> bytes:
    path = Path(data_dir) / "mapping_local_secret.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip().encode("utf-8")
    secret = stable_hash({"purpose": "marginpilot local fixture mapping", "data_dir": str(Path(data_dir).resolve())})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret + "\n", encoding="utf-8")
    return secret.encode("utf-8")
