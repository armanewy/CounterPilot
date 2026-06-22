from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field
import hashlib
import hmac
import json
from pathlib import Path
import secrets
from typing import Any, Mapping, Protocol

from behavior_lab.core import parse_time, stable_hash, utc_now
from behavior_lab.ledger import ImmutableLedger
from behavior_lab.marginpilot_storage.consent import (
    CROSS_MERCHANT_TRAINING,
    ConsentLedger,
    ConsentRequiredError,
    MERCHANT_SPECIFIC_MODEL_TRAINING,
)
from behavior_lab.marginpilot_storage.identifiers import EphemeralMappingLayer
from behavior_lab.marginpilot_storage.pii import BoundaryViolation, assert_no_pii


OPERATIONAL_COLLECTION = "marginpilot_operational_transactions"
OPERATIONAL_TOMBSTONE_COLLECTION = "marginpilot_operational_tombstones"
RESEARCH_RECORD_TYPE = "marginpilot_research_event"
OPERATIONAL_SCHEMA_VERSION = "marginpilot_operational_transaction.v1"
RESEARCH_SCHEMA_VERSION = "marginpilot_research_event.v1"

ALLOWED_OPERATIONAL_CUSTOMER_KEYS = {
    "billing_address",
    "customer_name",
    "email",
    "phone",
    "shipping_address",
}


class EncryptedAtRestAdapter(Protocol):
    """Storage adapter that persists only encrypted bytes outside the process."""

    def write(
        self,
        collection: str,
        record_id: str,
        plaintext: bytes,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        ...

    def read(self, collection: str, record_id: str) -> bytes | None:
        ...

    def delete(self, collection: str, record_id: str) -> bool:
        ...

    def list_record_ids(self, collection: str) -> list[str]:
        ...


class InMemoryEncryptedAtRestAdapter:
    """A deterministic local adapter for tests and offline development.

    This adapter stores ciphertext and an HMAC tag in memory. It is intentionally
    small, but the store only depends on the adapter protocol so production can
    supply a managed encrypted backend without changing research code.
    """

    def __init__(self, *, key: bytes | str | None = None):
        if key is None:
            key = secrets.token_bytes(32)
        if isinstance(key, str):
            key = key.encode("utf-8")
        if len(key) < 16:
            raise ValueError("encryption key must be at least 16 bytes")
        self._key = bytes(key)
        self._records: dict[tuple[str, str], dict[str, Any]] = {}

    def write(
        self,
        collection: str,
        record_id: str,
        plaintext: bytes,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        safe_metadata = dict(metadata or {})
        assert_no_pii(safe_metadata, label="encrypted adapter metadata")
        nonce = secrets.token_bytes(16)
        stream = _keystream(self._key, nonce, len(plaintext))
        ciphertext = _xor(plaintext, stream)
        tag = hmac.new(self._key, nonce + ciphertext, hashlib.sha256).hexdigest()
        self._records[(collection, record_id)] = {
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            "metadata": safe_metadata,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "tag": tag,
        }

    def read(self, collection: str, record_id: str) -> bytes | None:
        stored = self._records.get((collection, record_id))
        if stored is None:
            return None
        nonce = base64.b64decode(stored["nonce"])
        ciphertext = base64.b64decode(stored["ciphertext"])
        expected = hmac.new(self._key, nonce + ciphertext, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, str(stored["tag"])):
            raise BoundaryViolation("encrypted record authentication failed")
        return _xor(ciphertext, _keystream(self._key, nonce, len(ciphertext)))

    def delete(self, collection: str, record_id: str) -> bool:
        return self._records.pop((collection, record_id), None) is not None

    def list_record_ids(self, collection: str) -> list[str]:
        return sorted(record_id for stored_collection, record_id in self._records if stored_collection == collection)

    def raw_ciphertext(self, collection: str, record_id: str) -> bytes:
        stored = self._records[(collection, record_id)]
        return json.dumps(stored, sort_keys=True).encode("utf-8")


class LocalFileEncryptedAtRestAdapter:
    """Local encrypted adapter for development commands.

    This keeps direct operational identifiers out of research ledgers and plain
    JSON fixtures. It is a local development adapter, not a managed production
    key-management system.
    """

    def __init__(self, root: str | Path, *, key: bytes | str | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._key = self._load_or_create_key(key)

    def write(
        self,
        collection: str,
        record_id: str,
        plaintext: bytes,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        safe_metadata = dict(metadata or {})
        assert_no_pii(safe_metadata, label="encrypted adapter metadata")
        nonce = secrets.token_bytes(16)
        stream = _keystream(self._key, nonce, len(plaintext))
        ciphertext = _xor(plaintext, stream)
        tag = hmac.new(self._key, nonce + ciphertext, hashlib.sha256).hexdigest()
        body = {
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            "metadata": safe_metadata,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "tag": tag,
        }
        path = self._path(collection, record_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")

    def read(self, collection: str, record_id: str) -> bytes | None:
        path = self._path(collection, record_id)
        if not path.exists():
            return None
        stored = json.loads(path.read_text(encoding="utf-8"))
        nonce = base64.b64decode(stored["nonce"])
        ciphertext = base64.b64decode(stored["ciphertext"])
        expected = hmac.new(self._key, nonce + ciphertext, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, str(stored["tag"])):
            raise BoundaryViolation("encrypted record authentication failed")
        return _xor(ciphertext, _keystream(self._key, nonce, len(ciphertext)))

    def delete(self, collection: str, record_id: str) -> bool:
        path = self._path(collection, record_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def list_record_ids(self, collection: str) -> list[str]:
        collection_dir = self.root / _safe_path_segment(collection)
        if not collection_dir.exists():
            return []
        return sorted(path.stem for path in collection_dir.glob("*.json"))

    def raw_ciphertext(self, collection: str, record_id: str) -> bytes:
        return self._path(collection, record_id).read_bytes()

    def _load_or_create_key(self, key: bytes | str | None) -> bytes:
        if key is not None:
            if isinstance(key, str):
                key = key.encode("utf-8")
            if len(key) < 16:
                raise ValueError("encryption key must be at least 16 bytes")
            return bytes(key)
        key_path = self.root / "local_adapter.key"
        if key_path.exists():
            return base64.b64decode(key_path.read_text(encoding="utf-8").strip())
        generated = secrets.token_bytes(32)
        key_path.write_text(base64.b64encode(generated).decode("ascii") + "\n", encoding="utf-8")
        return generated

    def _path(self, collection: str, record_id: str) -> Path:
        return self.root / _safe_path_segment(collection) / f"{_safe_path_segment(record_id)}.json"


@dataclass(frozen=True)
class OperationalTransactionRecord:
    merchant_id: str
    store_id: str
    operational_transaction_id: str
    shopify_resource_ids: dict[str, str]
    contact_delivery_reference: str
    checkout_url_reference: str
    fulfillment_state: str
    payment_state: str
    retention_policy: str
    retention_expires_at: str | None = None
    operational_customer_data: dict[str, Any] = field(default_factory=dict)
    deleted_at: str | None = None
    deletion_reason: str | None = None
    deletion_provenance: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in [
            "merchant_id",
            "store_id",
            "operational_transaction_id",
            "contact_delivery_reference",
            "checkout_url_reference",
            "fulfillment_state",
            "payment_state",
            "retention_policy",
        ]:
            _require_nonempty(getattr(self, field_name), field_name)
        if not isinstance(self.shopify_resource_ids, dict) or not self.shopify_resource_ids:
            raise ValueError("shopify_resource_ids must be a non-empty object")
        if not all(isinstance(key, str) and key.strip() for key in self.shopify_resource_ids):
            raise ValueError("shopify_resource_ids keys must be non-empty strings")
        if not all(isinstance(value, str) and value.strip() for value in self.shopify_resource_ids.values()):
            raise ValueError("shopify_resource_ids values must be non-empty strings")
        if self.retention_expires_at is not None:
            parse_time(self.retention_expires_at)
        if self.deleted_at is not None:
            parse_time(self.deleted_at)
        if not isinstance(self.operational_customer_data, dict):
            raise ValueError("operational_customer_data must be an object")
        extra_customer_keys = sorted(set(self.operational_customer_data) - ALLOWED_OPERATIONAL_CUSTOMER_KEYS)
        if extra_customer_keys:
            raise ValueError(f"operational_customer_data contains unsupported keys: {extra_customer_keys}")
        if not isinstance(self.deletion_provenance, dict) or not isinstance(self.provenance, dict):
            raise ValueError("provenance fields must be objects")

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = OPERATIONAL_SCHEMA_VERSION
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "OperationalTransactionRecord":
        body = dict(payload)
        body.pop("schema_version", None)
        return cls(**body)


class OperationalTransactionStore:
    """Commerce-only operational records behind encrypted storage."""

    def __init__(self, adapter: EncryptedAtRestAdapter):
        self.adapter = adapter

    def put(self, record: OperationalTransactionRecord) -> str:
        payload = record.to_payload()
        storage_id = self.storage_id(
            merchant_id=record.merchant_id,
            store_id=record.store_id,
            operational_transaction_id=record.operational_transaction_id,
        )
        self.adapter.write(
            OPERATIONAL_COLLECTION,
            storage_id,
            json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8"),
            metadata={
                "merchant_id": record.merchant_id,
                "schema_version": OPERATIONAL_SCHEMA_VERSION,
                "store_id": record.store_id,
            },
        )
        return storage_id

    def get(
        self,
        *,
        merchant_id: str,
        store_id: str,
        operational_transaction_id: str,
    ) -> OperationalTransactionRecord | None:
        raw = self.adapter.read(
            OPERATIONAL_COLLECTION,
            self.storage_id(
                merchant_id=merchant_id,
                store_id=store_id,
                operational_transaction_id=operational_transaction_id,
            ),
        )
        if raw is None:
            return None
        return OperationalTransactionRecord.from_payload(json.loads(raw.decode("utf-8")))

    def delete_customer_data(
        self,
        *,
        merchant_id: str,
        store_id: str,
        operational_transaction_id: str,
        deleted_at: str | None = None,
        reason: str = "customer_data_deletion",
    ) -> dict[str, Any]:
        deletion_time = deleted_at or utc_now()
        parse_time(deletion_time)
        storage_id = self.storage_id(
            merchant_id=merchant_id,
            store_id=store_id,
            operational_transaction_id=operational_transaction_id,
        )
        existed = self.adapter.delete(OPERATIONAL_COLLECTION, storage_id)
        tombstone = {
            "deleted_at": deletion_time,
            "deletion_reason": reason,
            "merchant_id": merchant_id,
            "operational_storage_id": storage_id,
            "schema_version": "marginpilot_operational_tombstone.v1",
            "store_id": store_id,
        }
        self.adapter.write(
            OPERATIONAL_TOMBSTONE_COLLECTION,
            storage_id,
            json.dumps(tombstone, sort_keys=True, ensure_ascii=True).encode("utf-8"),
            metadata={"merchant_id": merchant_id, "schema_version": "marginpilot_operational_tombstone.v1", "store_id": store_id},
        )
        return {"deleted": existed, "tombstone": tombstone}

    def retention_due(self, *, as_of: str) -> list[OperationalTransactionRecord]:
        cutoff = parse_time(as_of)
        due: list[OperationalTransactionRecord] = []
        for record_id in self.adapter.list_record_ids(OPERATIONAL_COLLECTION):
            raw = self.adapter.read(OPERATIONAL_COLLECTION, record_id)
            if raw is None:
                continue
            record = OperationalTransactionRecord.from_payload(json.loads(raw.decode("utf-8")))
            if record.retention_expires_at is not None and parse_time(record.retention_expires_at) <= cutoff:
                due.append(record)
        return due

    @staticmethod
    def storage_id(*, merchant_id: str, store_id: str, operational_transaction_id: str) -> str:
        return "op_" + stable_hash(
            {
                "merchant_id": merchant_id,
                "operational_transaction_id": operational_transaction_id,
                "store_id": store_id,
            }
        )[:32]


@dataclass(frozen=True)
class ResearchEventRecord:
    event_id: str
    merchant_id: str
    store_id: str
    occurred_at: str
    pseudonymous_session_id: str
    pseudonymous_buyer_id: str
    offer_context: dict[str, Any]
    decisions: dict[str, Any]
    outcomes: dict[str, Any]
    financial_components: dict[str, Any]
    source_lineage: dict[str, Any]
    consent_policy_version: str | None = None
    consent_policy_hash: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in [
            "event_id",
            "merchant_id",
            "store_id",
            "occurred_at",
            "pseudonymous_session_id",
            "pseudonymous_buyer_id",
        ]:
            _require_nonempty(getattr(self, field_name), field_name)
        parse_time(self.occurred_at)
        for field_name in ["offer_context", "decisions", "outcomes", "financial_components", "source_lineage", "provenance"]:
            if not isinstance(getattr(self, field_name), dict):
                raise ValueError(f"{field_name} must be an object")
        for field_name in ["offer_context", "decisions", "outcomes", "financial_components"]:
            _validate_research_values(getattr(self, field_name), path=field_name)

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = RESEARCH_SCHEMA_VERSION
        assert_no_pii(payload, label="research event")
        return payload

    @classmethod
    def from_operational(
        cls,
        record: OperationalTransactionRecord,
        mapping_layer: EphemeralMappingLayer,
        *,
        event_id: str,
        occurred_at: str,
        offer_context: dict[str, Any],
        decisions: dict[str, Any],
        outcomes: dict[str, Any],
        financial_components: dict[str, Any],
        consent_policy_version: str | None = None,
        consent_policy_hash: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> "ResearchEventRecord":
        session = mapping_layer.transform(
            record.shopify_resource_ids.get("checkout_gid") or record.operational_transaction_id,
            merchant_id=record.merchant_id,
            store_id=record.store_id,
            namespace="session",
        )
        buyer_raw = (
            record.shopify_resource_ids.get("customer_gid")
            or record.operational_customer_data.get("email")
            or record.operational_transaction_id
        )
        buyer = mapping_layer.transform(
            buyer_raw,
            merchant_id=record.merchant_id,
            store_id=record.store_id,
            namespace="buyer",
        )
        source = mapping_layer.transform(
            record.operational_transaction_id,
            merchant_id=record.merchant_id,
            store_id=record.store_id,
            namespace="transaction",
        )
        return cls(
            event_id=event_id,
            merchant_id=record.merchant_id,
            store_id=record.store_id,
            occurred_at=occurred_at,
            pseudonymous_session_id=session.pseudonymous_id,
            pseudonymous_buyer_id=buyer.pseudonymous_id,
            offer_context=dict(offer_context),
            decisions=dict(decisions),
            outcomes=dict(outcomes),
            financial_components=dict(financial_components),
            source_lineage={
                "identifier_rotation_ids": sorted({session.rotation_id, buyer.rotation_id, source.rotation_id}),
                "operational_transform": "ephemeral_hmac_context_bound",
                "source_transaction_pseudonym": source.pseudonymous_id,
            },
            consent_policy_version=consent_policy_version,
            consent_policy_hash=consent_policy_hash,
            provenance=provenance or {"source": "operational_export"},
        )


@dataclass(frozen=True)
class TrainingDataset:
    purpose: str
    rows: tuple[dict[str, Any], ...]
    dataset_lineage: dict[str, Any]
    consent_lineage: tuple[dict[str, Any], ...]


class ResearchEventStore:
    """Append-only research ledger with no operational-store dependency."""

    def __init__(self, path: str | Path, *, consent_ledger: ConsentLedger):
        self.ledger = ImmutableLedger(path)
        self.consent_ledger = consent_ledger

    def append(self, event: ResearchEventRecord) -> dict[str, Any]:
        payload = event.to_payload()
        record_id = f"marginpilot_research_{stable_hash(payload)[:24]}"
        return self.ledger.append(RESEARCH_RECORD_TYPE, payload, record_id=record_id, unique_record_id=True)

    def events(self) -> list[dict[str, Any]]:
        return [dict(event) for event in self.ledger.payloads(RESEARCH_RECORD_TYPE)]

    def training_dataset(
        self,
        *,
        purpose: str = MERCHANT_SPECIFIC_MODEL_TRAINING,
        merchant_id: str | None = None,
        store_id: str | None = None,
        cross_merchant: bool = False,
        as_of: str | None = None,
    ) -> TrainingDataset:
        events = self.events()
        if merchant_id is not None:
            events = [event for event in events if event.get("merchant_id") == merchant_id]
        if store_id is not None:
            events = [event for event in events if event.get("store_id") == store_id]
        pairs = sorted({(str(event["merchant_id"]), str(event["store_id"])) for event in events})
        if not pairs:
            return TrainingDataset(
                purpose=purpose,
                rows=(),
                dataset_lineage={"dataset_id": stable_hash({"events": [], "purpose": purpose}), "event_hashes": []},
                consent_lineage=(),
            )
        if cross_merchant:
            if purpose != CROSS_MERCHANT_TRAINING:
                raise ConsentRequiredError("cross-merchant training requires the cross-merchant purpose")
        elif len({merchant for merchant, _ in pairs}) > 1 or merchant_id is None:
            raise ConsentRequiredError("cross-merchant training is prohibited by default")
        consent_lineage = self.consent_ledger.lineage_for(pairs, purpose=purpose, as_of=as_of)
        event_hashes = [stable_hash(event) for event in events]
        rows = tuple(self._training_row(event) for event in events)
        payload = {
            "consent_lineage": consent_lineage,
            "dataset_lineage": {
                "dataset_id": stable_hash({"event_hashes": event_hashes, "pairs": pairs, "purpose": purpose}),
                "event_count": len(events),
                "event_hashes": event_hashes,
                "merchant_store_pairs": [{"merchant_id": merchant, "store_id": store} for merchant, store in pairs],
                "research_record_type": RESEARCH_RECORD_TYPE,
            },
            "purpose": purpose,
            "rows": rows,
        }
        assert_no_pii(payload, label="training dataset")
        return TrainingDataset(
            purpose=purpose,
            rows=rows,
            dataset_lineage=payload["dataset_lineage"],
            consent_lineage=tuple(consent_lineage),
        )

    def _training_row(self, event: dict[str, Any]) -> dict[str, Any]:
        features = dict(event["offer_context"])
        features.update({f"financial_{key}": value for key, value in event["financial_components"].items()})
        row = {
            "decision": dict(event["decisions"]),
            "event_hash": stable_hash(event),
            "features": features,
            "merchant_id": event["merchant_id"],
            "outcome": dict(event["outcomes"]),
            "pseudonymous_buyer_id": event["pseudonymous_buyer_id"],
            "pseudonymous_session_id": event["pseudonymous_session_id"],
            "store_id": event["store_id"],
        }
        assert_no_pii(row, label="training row")
        return row


def build_model_feature_matrix(source: TrainingDataset) -> list[dict[str, Any]]:
    if isinstance(source, OperationalTransactionStore):
        raise BoundaryViolation("model features must be built from research datasets, not operational stores")
    if not isinstance(source, TrainingDataset):
        raise BoundaryViolation("model features require a MarginPilot TrainingDataset")
    matrix = [dict(row["features"]) for row in source.rows]
    assert_no_pii(matrix, label="model feature matrix")
    return matrix


def production_artifact_manifest(
    *,
    artifact_id: str,
    model_id: str,
    dataset: TrainingDataset,
    created_at: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _require_nonempty(artifact_id, "artifact_id")
    _require_nonempty(model_id, "model_id")
    if not dataset.dataset_lineage or not dataset.consent_lineage:
        raise BoundaryViolation("production artifacts require dataset and consent lineage")
    payload = {
        "artifact_id": artifact_id,
        "consent_lineage": list(dataset.consent_lineage),
        "created_at": created_at or utc_now(),
        "dataset_lineage": dict(dataset.dataset_lineage),
        "metrics": dict(metrics or {}),
        "model_id": model_id,
        "purpose": dataset.purpose,
        "schema_version": "marginpilot_production_artifact.v1",
    }
    parse_time(payload["created_at"])
    assert_no_pii(payload, label="production artifact")
    return payload


def _require_nonempty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")


MONEY_KEY_TOKENS = {
    "amount",
    "basis",
    "cost",
    "discount",
    "fee",
    "fees",
    "floor",
    "fulfillment",
    "margin",
    "price",
    "proceeds",
    "refund",
    "shipping",
}


def _validate_research_values(value: Any, *, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            key_tokens = _key_tokens(key_text)
            child_path = f"{path}.{key_text}"
            if _is_research_money_field(key_text, key_tokens):
                if not key_text.endswith("_minor"):
                    raise ValueError(f"research monetary field must use integer minor units: {child_path}")
                if isinstance(item, bool) or not isinstance(item, int):
                    raise ValueError(f"research monetary field must be integer minor units: {child_path}")
            _validate_research_values(item, path=child_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_research_values(item, path=f"{path}[{index}]")
    elif isinstance(value, float):
        raise ValueError(f"research values may not use floats; use integer minor units where monetary: {path}")


def _key_tokens(value: str) -> set[str]:
    text = value.lower()
    for separator in [".", "-", ":", "/", "\\", " "]:
        text = text.replace(separator, "_")
    return {token for token in text.split("_") if token}


def _is_research_money_field(key_text: str, key_tokens: set[str]) -> bool:
    lower = key_text.lower()
    if lower.endswith(("_state", "_status", "_date", "_at", "_id", "_ids", "_flag")):
        return False
    return bool(key_tokens & MONEY_KEY_TOKENS)


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = b""
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return out[:length]


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))


def _safe_path_segment(value: str) -> str:
    return "".join(character if character.isalnum() or character in {"_", "-"} else "_" for character in value)
