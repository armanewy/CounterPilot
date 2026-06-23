from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from behavior_lab.core import parse_time, stable_hash, utc_now
from behavior_lab.ledger import ImmutableLedger
from behavior_lab.counterpilot_storage.pii import BoundaryViolation, assert_no_pii


MERCHANT_SPECIFIC_MODEL_TRAINING = "merchant_specific_model_training"
MERCHANT_SPECIFIC_SHADOW_RECOMMENDATIONS = "merchant_specific_shadow_recommendations"
CROSS_MERCHANT_TRAINING = "cross_merchant_training"
PRODUCTION_ARTIFACT_EXPORT = "production_artifact_export"
POLICY_EXPERIMENTS = "merchant_specific_policy_experiments"

CONSENT_RECORD_TYPE = "counterpilot_consent_record"


class ConsentRequiredError(BoundaryViolation):
    pass


@dataclass(frozen=True)
class ConsentRecord:
    merchant_id: str
    store_id: str
    consent_policy_version: str
    policy_hash: str
    granted_purposes: tuple[str, ...] = field(default_factory=tuple)
    prohibited_purposes: tuple[str, ...] = field(default_factory=tuple)
    granted_at: str = field(default_factory=utc_now)
    revoked_at: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonempty(self.merchant_id, "merchant_id")
        _require_nonempty(self.store_id, "store_id")
        _require_nonempty(self.consent_policy_version, "consent_policy_version")
        _require_nonempty(self.policy_hash, "policy_hash")
        parse_time(self.granted_at)
        if self.revoked_at is not None:
            if parse_time(self.revoked_at) < parse_time(self.granted_at):
                raise ValueError("revoked_at may not be before granted_at")
        granted = _normalize_purposes(self.granted_purposes, "granted_purposes")
        prohibited = _normalize_purposes(self.prohibited_purposes, "prohibited_purposes")
        overlap = set(granted) & set(prohibited)
        if overlap:
            raise ValueError(f"purposes cannot be both granted and prohibited: {sorted(overlap)}")
        if not isinstance(self.provenance, dict):
            raise ValueError("provenance must be an object")
        object.__setattr__(self, "granted_purposes", granted)
        object.__setattr__(self, "prohibited_purposes", prohibited)
        object.__setattr__(self, "provenance", dict(self.provenance))

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


class ConsentLedger:
    """Append-only consent records with purpose-specific active checks."""

    def __init__(self, path: str | Path):
        self.ledger = ImmutableLedger(path)

    def append(self, record: ConsentRecord) -> dict[str, Any]:
        payload = record.to_payload()
        assert_no_pii(payload, label="consent record")
        record_id = "counterpilot_consent_" + stable_hash(payload)[:24]
        return self.ledger.append(CONSENT_RECORD_TYPE, payload, record_id=record_id, unique_record_id=True)

    def revoke(
        self,
        *,
        merchant_id: str,
        store_id: str,
        purpose: str,
        revoked_at: str,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous = self.latest_evidence(
            merchant_id=merchant_id,
            store_id=store_id,
            purpose=purpose,
            require_active=False,
            as_of=revoked_at,
        )
        policy_version = str(previous.get("consent_policy_version") or "unknown")
        policy_hash = str(previous.get("policy_hash") or "unknown")
        return self.append(
            ConsentRecord(
                merchant_id=merchant_id,
                store_id=store_id,
                consent_policy_version=policy_version,
                policy_hash=policy_hash,
                granted_purposes=(),
                prohibited_purposes=(purpose,),
                granted_at=revoked_at,
                revoked_at=revoked_at,
                provenance=provenance or {"source": "revocation"},
            )
        )

    def records(self) -> list[dict[str, Any]]:
        return [dict(record["payload"]) for record in self.ledger.scan(CONSENT_RECORD_TYPE)]

    def is_active(
        self,
        *,
        merchant_id: str,
        store_id: str,
        purpose: str,
        as_of: str | None = None,
    ) -> bool:
        state = False
        cutoff = parse_time(as_of or utc_now())
        for record in self._effective_records(merchant_id=merchant_id, store_id=store_id, purpose=purpose, cutoff=cutoff):
            payload = record["payload"]
            granted = set(payload.get("granted_purposes") or [])
            prohibited = set(payload.get("prohibited_purposes") or [])
            revoked_at = payload.get("revoked_at")
            revoked = revoked_at is not None and parse_time(str(revoked_at)) <= cutoff
            if purpose in prohibited:
                state = False
            if purpose in granted:
                state = not revoked
            if revoked and (purpose in granted or purpose in prohibited):
                state = False
        return state

    def require_active(
        self,
        *,
        merchant_id: str,
        store_id: str,
        purpose: str,
        as_of: str | None = None,
    ) -> None:
        if not self.is_active(merchant_id=merchant_id, store_id=store_id, purpose=purpose, as_of=as_of):
            raise ConsentRequiredError("active purpose-specific consent is required")

    def latest_evidence(
        self,
        *,
        merchant_id: str,
        store_id: str,
        purpose: str,
        require_active: bool = True,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        if require_active:
            self.require_active(merchant_id=merchant_id, store_id=store_id, purpose=purpose, as_of=as_of)
        cutoff = parse_time(as_of or utc_now())
        latest: dict[str, Any] | None = None
        for record in self._effective_records(merchant_id=merchant_id, store_id=store_id, purpose=purpose, cutoff=cutoff):
            payload = record["payload"]
            if purpose not in set(payload.get("granted_purposes") or []) | set(payload.get("prohibited_purposes") or []):
                continue
            latest = {
                "active": self.is_active(merchant_id=merchant_id, store_id=store_id, purpose=purpose, as_of=as_of),
                "consent_policy_version": payload.get("consent_policy_version"),
                "granted_at": payload.get("granted_at"),
                "granted_purposes": list(payload.get("granted_purposes") or []),
                "merchant_id": merchant_id,
                "policy_hash": payload.get("policy_hash"),
                "prohibited_purposes": list(payload.get("prohibited_purposes") or []),
                "purpose": purpose,
                "record_hash": record.get("record_hash"),
                "record_id": record.get("record_id"),
                "revoked_at": payload.get("revoked_at"),
                "store_id": store_id,
            }
        if latest is None:
            return {
                "active": False,
                "consent_policy_version": None,
                "merchant_id": merchant_id,
                "policy_hash": None,
                "purpose": purpose,
                "record_hash": None,
                "record_id": None,
                "store_id": store_id,
            }
        return latest

    def lineage_for(
        self,
        merchant_store_pairs: Iterable[tuple[str, str]],
        *,
        purpose: str,
        as_of: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            self.latest_evidence(merchant_id=merchant_id, store_id=store_id, purpose=purpose, as_of=as_of)
            for merchant_id, store_id in sorted(set(merchant_store_pairs))
        ]

    def _matching_records(self, *, merchant_id: str, store_id: str) -> list[dict[str, Any]]:
        return [
            record
            for record in self.ledger.scan(CONSENT_RECORD_TYPE)
            if record["payload"].get("merchant_id") == merchant_id and record["payload"].get("store_id") == store_id
        ]

    def _effective_records(self, *, merchant_id: str, store_id: str, purpose: str, cutoff: object) -> list[dict[str, Any]]:
        records = []
        for record in self._matching_records(merchant_id=merchant_id, store_id=store_id):
            payload = record["payload"]
            granted_at = parse_time(str(payload["granted_at"]))
            if granted_at <= cutoff:
                records.append(record)
        return sorted(
            records,
            key=lambda record: (
                parse_time(str(record["payload"]["granted_at"])),
                _purpose_prohibition_sort_order(record["payload"], purpose),
                parse_time(str(record["written_at"])),
                str(record["record_id"]),
            ),
        )


def _normalize_purposes(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes)):
        raise ValueError(f"{field_name} must be a collection")
    normalized = tuple(sorted({str(value).strip() for value in values if str(value).strip()}))
    return normalized


def _require_nonempty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")


def _purpose_prohibition_sort_order(payload: dict[str, Any], purpose: str) -> int:
    prohibited = set(payload.get("prohibited_purposes") or [])
    granted = set(payload.get("granted_purposes") or [])
    revoked_at = payload.get("revoked_at")
    relevant_revocation = revoked_at is not None and purpose in (prohibited | granted)
    return 1 if purpose in prohibited or relevant_revocation else 0
