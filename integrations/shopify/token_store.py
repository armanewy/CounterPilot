from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from behavior_lab.core import parse_time, stable_hash
from behavior_lab.counterpilot_storage import EncryptedAtRestAdapter, LocalFileEncryptedAtRestAdapter


SHOPIFY_TOKEN_SCHEMA_VERSION = "counterpilot.shopify_token.v1"
SHOPIFY_TOKEN_COLLECTION = "counterpilot_shopify_tokens"

REQUIRED_DEVELOPMENT_SCOPES = (
    "read_orders",
    "read_products",
    "write_draft_orders",
)


@dataclass(frozen=True)
class ShopifyTokenRecord:
    merchant_id: str
    store_id: str
    store_domain: str
    access_token: str
    scopes: tuple[str, ...]
    installed_at: str
    revoked_at: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ["merchant_id", "store_id", "store_domain", "access_token", "installed_at"]:
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty")
        parse_time(self.installed_at)
        if self.revoked_at is not None:
            parse_time(self.revoked_at)
        normalized = tuple(sorted({str(scope).strip() for scope in self.scopes if str(scope).strip()}))
        missing = sorted(set(REQUIRED_DEVELOPMENT_SCOPES) - set(normalized))
        if missing:
            raise ValueError(f"missing required least-privilege Shopify scopes: {missing}")
        object.__setattr__(self, "scopes", normalized)

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = SHOPIFY_TOKEN_SCHEMA_VERSION
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ShopifyTokenRecord":
        body = dict(payload)
        body.pop("schema_version", None)
        body["scopes"] = tuple(body.get("scopes") or ())
        return cls(**body)


class ShopifyTokenStore:
    def __init__(self, adapter: EncryptedAtRestAdapter):
        self.adapter = adapter

    @classmethod
    def local(cls, root: str | Path) -> "ShopifyTokenStore":
        return cls(LocalFileEncryptedAtRestAdapter(Path(root) / "shopify_tokens_encrypted"))

    def put(self, record: ShopifyTokenRecord) -> str:
        record_id = self.record_id(merchant_id=record.merchant_id, store_id=record.store_id)
        self.adapter.write(
            SHOPIFY_TOKEN_COLLECTION,
            record_id,
            json.dumps(record.to_payload(), sort_keys=True, ensure_ascii=True).encode("utf-8"),
            metadata={
                "merchant_id": record.merchant_id,
                "schema_version": SHOPIFY_TOKEN_SCHEMA_VERSION,
                "store_id": record.store_id,
            },
        )
        return record_id

    def get(self, *, merchant_id: str, store_id: str) -> ShopifyTokenRecord | None:
        raw = self.adapter.read(SHOPIFY_TOKEN_COLLECTION, self.record_id(merchant_id=merchant_id, store_id=store_id))
        if raw is None:
            return None
        return ShopifyTokenRecord.from_payload(json.loads(raw.decode("utf-8")))

    def revoke(self, *, merchant_id: str, store_id: str, revoked_at: str, provenance: dict[str, Any] | None = None) -> dict[str, Any]:
        existing = self.get(merchant_id=merchant_id, store_id=store_id)
        if existing is None:
            return {"revoked": False, "record_id": self.record_id(merchant_id=merchant_id, store_id=store_id)}
        parse_time(revoked_at)
        updated = ShopifyTokenRecord(
            merchant_id=existing.merchant_id,
            store_id=existing.store_id,
            store_domain=existing.store_domain,
            access_token=existing.access_token,
            scopes=existing.scopes,
            installed_at=existing.installed_at,
            revoked_at=revoked_at,
            provenance=provenance or {"source": "shopify_app_uninstalled"},
        )
        record_id = self.put(updated)
        return {"revoked": True, "record_id": record_id}

    @staticmethod
    def record_id(*, merchant_id: str, store_id: str) -> str:
        return "shopify_token_" + stable_hash({"merchant_id": merchant_id, "store_id": store_id})[:32]
