from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import re
import secrets
from typing import Any

from behavior_lab.core import stable_json


@dataclass(frozen=True)
class PseudonymousIdentifier:
    pseudonymous_id: str
    rotation_id: str
    namespace: str


@dataclass(frozen=True)
class _MappingEntry:
    merchant_id: str
    store_id: str
    namespace: str
    raw_identifier: str
    pseudonymous_id: str
    rotation_id: str


class EphemeralMappingLayer:
    """Context-bound, rotatable identity mapping for research export.

    The mapping is intentionally process-local. Research events keep the
    pseudonymous identifier; operational systems keep the direct identifiers.
    Deleting this layer must not make already-exported research events unusable.
    """

    def __init__(self, *, secret: bytes | str | None = None, rotation_id: str = "rotation_001"):
        self.active_rotation_id = _require_nonempty(rotation_id, "rotation_id")
        self._rotations: dict[str, bytes] = {self.active_rotation_id: _normalize_secret(secret)}
        self._entries: dict[str, _MappingEntry] = {}

    @property
    def active_count(self) -> int:
        return len(self._entries)

    def rotate(self, *, rotation_id: str | None = None, secret: bytes | str | None = None) -> str:
        next_rotation = _require_nonempty(rotation_id or f"rotation_{len(self._rotations) + 1:03d}", "rotation_id")
        if next_rotation in self._rotations:
            raise ValueError("rotation_id already exists")
        self._rotations[next_rotation] = _normalize_secret(secret)
        self.active_rotation_id = next_rotation
        return next_rotation

    def transform(
        self,
        raw_identifier: Any,
        *,
        merchant_id: str,
        store_id: str,
        namespace: str,
    ) -> PseudonymousIdentifier:
        raw_text = _require_nonempty(str(raw_identifier), "raw_identifier")
        merchant = _require_nonempty(merchant_id, "merchant_id")
        store = _require_nonempty(store_id, "store_id")
        mapped_namespace = _slug(_require_nonempty(namespace, "namespace"))
        rotation_id = self.active_rotation_id
        secret = self._rotations[rotation_id]
        body = stable_json(
            {
                "merchant_id": merchant,
                "namespace": mapped_namespace,
                "raw_identifier": raw_text,
                "rotation_id": rotation_id,
                "store_id": store,
            }
        ).encode("utf-8")
        digest = hmac.new(secret, body, hashlib.sha256).hexdigest()
        pseudonymous_id = f"mp_{mapped_namespace}_{digest[:32]}"
        self._entries[pseudonymous_id] = _MappingEntry(
            merchant_id=merchant,
            store_id=store,
            namespace=mapped_namespace,
            raw_identifier=raw_text,
            pseudonymous_id=pseudonymous_id,
            rotation_id=rotation_id,
        )
        return PseudonymousIdentifier(
            pseudonymous_id=pseudonymous_id,
            rotation_id=rotation_id,
            namespace=mapped_namespace,
        )

    def resolve(self, pseudonymous_id: str) -> str | None:
        entry = self._entries.get(pseudonymous_id)
        return entry.raw_identifier if entry is not None else None

    def delete_mapping(self, pseudonymous_id: str) -> bool:
        return self._entries.pop(pseudonymous_id, None) is not None

    def delete_subject(
        self,
        raw_identifier: Any,
        *,
        merchant_id: str,
        store_id: str,
        namespace: str | None = None,
    ) -> int:
        raw_text = str(raw_identifier)
        mapped_namespace = _slug(namespace) if namespace is not None else None
        removed = 0
        for pseudonymous_id, entry in list(self._entries.items()):
            if entry.raw_identifier != raw_text or entry.merchant_id != merchant_id or entry.store_id != store_id:
                continue
            if mapped_namespace is not None and entry.namespace != mapped_namespace:
                continue
            del self._entries[pseudonymous_id]
            removed += 1
        return removed

    def delete_rotation(self, rotation_id: str) -> int:
        removed = 0
        for pseudonymous_id, entry in list(self._entries.items()):
            if entry.rotation_id == rotation_id:
                del self._entries[pseudonymous_id]
                removed += 1
        self._rotations.pop(rotation_id, None)
        if self.active_rotation_id == rotation_id:
            self.active_rotation_id = next(iter(self._rotations), "rotation_deleted")
        return removed


def _normalize_secret(secret: bytes | str | None) -> bytes:
    if secret is None:
        return secrets.token_bytes(32)
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if len(secret) < 16:
        raise ValueError("mapping secret must be at least 16 bytes")
    return bytes(secret)


def _slug(value: str | None) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return text or "identifier"


def _require_nonempty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value.strip()
