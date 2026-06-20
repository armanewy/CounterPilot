from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import time
from typing import Any, Protocol
from uuid import uuid4

from behavior_lab import __version__
from behavior_lab.bridge import (
    BRIDGE_SCHEMA_VERSION,
    CAMPAIGN_001_FEATURES,
    CAMPAIGN_001_ID,
    CAMPAIGN_001_OUTCOMES,
    CAMPAIGN_001_SCHEMA_VERSION,
    CAMPAIGN_001_TARGET,
    BridgeImportResult,
    BridgeValidationError,
    ensure_campaign_definition,
    import_snapshot_file,
    validate_snapshot,
    validate_snapshot_file,
    with_source_hash,
)
from behavior_lab.core import parse_time, stable_hash, utc_now
from behavior_lab.ledger import DuplicateRecordError, ImmutableLedger
from behavior_lab.temporal import assert_feature_map_is_pre_decision


CAPTURE_SCHEMA_VERSION = "campaign_001_task_initiation_capture.v1"
AUDIT_SCHEMA_VERSION = "campaign_001_episode_audit.v1"
AUDIT_RECORD_TYPE = "campaign_001_episode_audit"
ELIGIBILITY_RULE_VERSION = "campaign_001_eligibility.v1.1"
ELIGIBILITY_RULE_TEXT = (
    "Record any self-directed task expected to require at least ten minutes when I genuinely intend "
    "to begin it within the next fifteen minutes. Exclude emergencies, meetings already in progress, "
    "trivial actions, and tasks someone else is actively directing."
)
DEFAULT_AVAILABLE_ACTIONS = ["start_now", "defer", "switch_task", "abandon"]
DEFAULT_DATA_DIR = Path("data") / CAMPAIGN_001_ID
FIELD_SOURCE_STATUSES = {"manual", "derived", "unavailable"}
OUTCOME_SOURCE_STATUSES = {"manual_observation", "timer_assisted", "system_assisted", "unavailable"}
EPISODE_STATUSES = {"active", "completed", "incomplete", "missed_followup", "invalidated"}
FEATURE_NAMES = list(CAMPAIGN_001_FEATURES)
OUTCOME_NAMES = list(CAMPAIGN_001_OUTCOMES)
OUTCOME_TO_FOLLOWUP = {
    "started_within_10_minutes": "t_plus_10",
    "start_latency_seconds": "t_plus_10",
    "worked_for_20_minutes": "t_plus_20",
    "completed_that_day": "end_of_day",
}


class CollectorError(ValueError):
    pass


class OutcomeSourceAdapter(Protocol):
    """Future interface for local, explicit outcome sources.

    The collector does not install any system monitoring.  Adapters must return
    user-visible values and source statuses from the declared outcome enum.
    """

    name: str

    def read_outcomes(self, episode: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class CollectorPaths:
    root: Path
    captures: Path
    bridge_exports: Path
    missed: Path
    ledger_dir: Path


def paths_for(data_dir: str | Path = DEFAULT_DATA_DIR) -> CollectorPaths:
    root = Path(data_dir)
    return CollectorPaths(
        root=root,
        captures=root / "captures",
        bridge_exports=root / "bridge_exports",
        missed=root / "missed",
        ledger_dir=root,
    )


def load_script(path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CollectorError("Script input must be a JSON object")
    return payload


def start_capture(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    *,
    script: dict[str, Any] | None = None,
    collection_phase: str | None = None,
    input_func: Any = input,
) -> dict[str, Any]:
    script = script if script is not None else _prompt_start(input_func)
    if collection_phase is not None:
        script = {**script, "collection_phase": collection_phase}
    _reject_outcomes_in_start(script)
    paths = paths_for(data_dir)
    recovered = recover_atomic_writes(paths.root)

    episode_uuid = str(script.get("episode_uuid") or uuid4())
    compact_uuid = episode_uuid.replace("-", "")
    episode_id = str(script.get("episode_id") or f"c001_{compact_uuid[:16]}")
    now = _script_time(script, "decision_time")
    observation_cutoff = str(script.get("observation_cutoff") or now)
    timezone_name = str(script.get("timezone") or _local_timezone_name())
    monotonic_start = float(script.get("monotonic_start", time.monotonic()))

    features, field_sources, missing_fields, feature_errors = _collect_features(script)
    available_actions = _available_actions(script)
    snapshot = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "campaign_schema_version": CAMPAIGN_001_SCHEMA_VERSION,
        "campaign_id": CAMPAIGN_001_ID,
        "episode_id": episode_id,
        "subject_id": str(script.get("subject_id") or "arman"),
        "decision_time": now,
        "observation_cutoff": observation_cutoff,
        "task_description": str(script.get("task_description") or features.get("task_type") or "unavailable"),
        "available_actions": available_actions,
        "pre_decision_features": features,
        "provenance": {
            "entry_method": "campaign_001_local_collector",
            "collector_schema_version": CAPTURE_SCHEMA_VERSION,
            "collector_version": __version__,
            "episode_uuid": episode_uuid,
            "timezone": timezone_name,
            "source_statuses": field_sources,
            "manual_note": script.get("manual_note"),
            "eligible_episode": bool(script.get("eligible_episode", True)),
            "eligibility_rule_version": str(script.get("eligibility_rule_version") or ELIGIBILITY_RULE_VERSION),
            "eligibility_rule": ELIGIBILITY_RULE_TEXT,
            "collection_phase": _collection_phase(script),
            "collection_mode": str(script.get("collection_mode") or "natural_observation"),
            "intervention": None,
        },
    }
    validation = validate_pre_decision_snapshot(snapshot, missing_fields=missing_fields, feature_errors=feature_errors)
    pre_decision_hash = stable_hash(snapshot)
    status = "active" if validation["valid"] and bool(script.get("eligible_episode", True)) else "incomplete"
    artifact = {
        "collector_schema_version": CAPTURE_SCHEMA_VERSION,
        "campaign_id": CAMPAIGN_001_ID,
        "episode_uuid": episode_uuid,
        "episode_id": episode_id,
        "collector_version": __version__,
        "created_at": now,
        "timezone": timezone_name,
        "monotonic_start": monotonic_start,
        "eligible_episode": bool(script.get("eligible_episode", True)),
        "eligibility_rule_version": str(script.get("eligibility_rule_version") or ELIGIBILITY_RULE_VERSION),
        "episode_status": status,
        "invalidation_reason": None,
        "sealed_pre_decision_snapshot": snapshot,
        "pre_decision_hash": pre_decision_hash,
        "pre_decision_sealed_at": utc_now(),
        "pre_decision_validation": validation,
        "followups": _followups_for(now),
        "amendments": [],
        "event_log": [
            {
                "event": "pre_decision_sealed",
                "recorded_at": utc_now(),
                "pre_decision_hash": pre_decision_hash,
                "validation": validation,
            }
        ],
    }
    artifact_path = _capture_path(paths, episode_id)
    if artifact_path.exists():
        existing = _read_json(artifact_path)
        if existing.get("pre_decision_hash") != pre_decision_hash:
            raise CollectorError(f"Capture already exists with a different pre-decision hash: {episode_id}")
        return _start_summary(existing, artifact_path, recovered=recovered, already_exists=True)
    atomic_write_json(artifact_path, artifact)
    if status == "incomplete":
        audit_record = _append_capture_audit(paths, artifact, artifact_path, "incomplete_pre_decision")
        artifact["audit_ledger_record_id"] = audit_record["record_id"]
        atomic_write_json(artifact_path, artifact)
    return _start_summary(artifact, artifact_path, recovered=recovered, already_exists=False)


def finalize_capture(
    episode_id: str,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    *,
    script: dict[str, Any] | None = None,
    input_func: Any = input,
) -> dict[str, Any]:
    paths = paths_for(data_dir)
    recover_atomic_writes(paths.root)
    artifact_path = _capture_path(paths, episode_id)
    artifact = _load_capture(artifact_path)
    if artifact.get("episode_status") == "invalidated":
        raise CollectorError("Invalidated episodes cannot be finalized")
    if artifact.get("episode_status") == "missed_followup" and artifact.get("bridge_import"):
        return _finalize_summary(artifact, artifact_path, already_imported=True)
    if artifact.get("episode_status") == "completed" and artifact.get("bridge_import"):
        return _finalize_summary(artifact, artifact_path, already_imported=True)
    validation = artifact.get("pre_decision_validation", {})
    if not validation.get("valid"):
        raise CollectorError(f"Cannot finalize an incomplete pre-decision capture: {validation}")
    if "sealed_pre_decision_snapshot" not in artifact or "pre_decision_hash" not in artifact:
        raise CollectorError("Cannot record outcomes before the pre-decision snapshot is sealed")

    script = script if script is not None else _prompt_outcome(input_func)
    outcome, outcome_sources, missing_outcomes = _collect_outcomes(script)
    if missing_outcomes:
        missed = deepcopy(artifact)
        missed["episode_status"] = "missed_followup"
        missed["protected_outcome"] = outcome
        missed["outcome_source_statuses"] = outcome_sources
        missed["missing_outcome_fields"] = missing_outcomes
        missed["followups"] = _complete_followups(missed["followups"], outcome_sources)
        missed["bridge_import"] = {
            "imported": False,
            "ledger": str(paths.ledger_dir / "ledger.jsonl"),
            "ledger_record_id": None,
            "source_hash": None,
            "snapshots": 0,
            "ledger_valid": _ledger_valid(paths.ledger_dir),
        }
        missed["event_log"] = list(missed.get("event_log", [])) + [
            {
                "event": "followup_unavailable",
                "recorded_at": utc_now(),
                "missing_outcome_fields": missing_outcomes,
            }
        ]
        atomic_write_json(artifact_path, missed)
        audit_record = _append_capture_audit(paths, missed, artifact_path, "missed_followup")
        missed["audit_ledger_record_id"] = audit_record["record_id"]
        atomic_write_json(artifact_path, missed)
        return _finalize_summary(missed, artifact_path, already_imported=False)

    snapshot = deepcopy(artifact["sealed_pre_decision_snapshot"])
    if stable_hash(snapshot) != artifact["pre_decision_hash"]:
        raise CollectorError("Stored pre-decision snapshot does not match its sealed hash")
    snapshot["protected_outcome"] = outcome
    snapshot["provenance"] = dict(snapshot["provenance"])
    snapshot["provenance"].update(
        {
            "pre_decision_hash": artifact["pre_decision_hash"],
            "outcome_source_statuses": outcome_sources,
            "outcome_recorded_at": str(script.get("recorded_at") or utc_now()),
        }
    )
    hashed_snapshot = with_source_hash(snapshot)
    validate_snapshot(hashed_snapshot, campaign_id=CAMPAIGN_001_ID)
    export_path = paths.bridge_exports / f"{episode_id}.jsonl"
    atomic_write_text(export_path, json.dumps(hashed_snapshot, sort_keys=True, ensure_ascii=True) + "\n")
    validate_snapshot_file(export_path, campaign_id=CAMPAIGN_001_ID)
    import_summary = import_export_idempotently(export_path, data_dir=paths.ledger_dir)

    finalized = deepcopy(artifact)
    finalized["episode_status"] = "completed"
    finalized["protected_outcome"] = outcome
    finalized["outcome_source_statuses"] = outcome_sources
    finalized["source_hash"] = hashed_snapshot["source_hash"]
    finalized["bridge_export"] = {
        "path": str(export_path),
        "source_hash": hashed_snapshot["source_hash"],
        "validated": True,
    }
    finalized["bridge_import"] = import_summary
    finalized["monotonic_elapsed_seconds"] = max(0.0, float(script.get("monotonic_end", time.monotonic())) - float(artifact["monotonic_start"]))
    finalized["followups"] = _complete_followups(finalized["followups"], outcome_sources)
    finalized["event_log"] = list(finalized.get("event_log", [])) + [
        {
            "event": "outcome_finalized",
            "recorded_at": utc_now(),
            "source_hash": hashed_snapshot["source_hash"],
            "ledger_record_id": import_summary["ledger_record_id"],
        }
    ]
    atomic_write_json(artifact_path, finalized)
    return _finalize_summary(finalized, artifact_path, already_imported=not import_summary["imported"])


def resume_capture(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    *,
    episode_id: str | None = None,
    script: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if episode_id is not None and script is not None:
        return finalize_capture(episode_id, data_dir, script=script)
    paths = paths_for(data_dir)
    captures = _load_all_captures(paths)
    active = [
        {
            "episode_id": item["episode_id"],
            "episode_status": item["episode_status"],
            "missing_fields": item.get("pre_decision_validation", {}).get("missing_fields", []),
        }
        for item in captures
        if item.get("episode_status") in {"active", "incomplete", "missed_followup"}
    ]
    if episode_id is not None:
        active = [item for item in active if item["episode_id"] == episode_id]
    return {"data_dir": str(paths.root.resolve()), "resumable_episodes": active}


def amend_capture(
    episode_id: str,
    field: str,
    value: Any,
    reason: str,
    data_dir: str | Path = DEFAULT_DATA_DIR,
) -> dict[str, Any]:
    if not str(reason).strip():
        raise CollectorError("Amendments require a reason")
    paths = paths_for(data_dir)
    artifact_path = _capture_path(paths, episode_id)
    artifact = _load_capture(artifact_path)
    original = _lookup_field(artifact, field)
    amendment = {
        "field": field,
        "original_value": original,
        "corrected_value": value,
        "reason": reason,
        "recorded_at": utc_now(),
        "sealed_pre_decision_hash": artifact.get("pre_decision_hash"),
    }
    artifact["amendments"] = list(artifact.get("amendments", [])) + [amendment]
    artifact["event_log"] = list(artifact.get("event_log", [])) + [{"event": "amendment_recorded", **amendment}]
    atomic_write_json(artifact_path, artifact)
    return {"episode_id": episode_id, "artifact_path": str(artifact_path.resolve()), "amendment": amendment}


def invalidate_capture(
    episode_id: str,
    reason: str,
    data_dir: str | Path = DEFAULT_DATA_DIR,
) -> dict[str, Any]:
    if not str(reason).strip():
        raise CollectorError("Invalidation requires a reason")
    paths = paths_for(data_dir)
    artifact_path = _capture_path(paths, episode_id)
    artifact = _load_capture(artifact_path)
    if artifact.get("episode_status") == "completed":
        raise CollectorError("Completed bridge-imported episodes cannot be invalidated by the collector")
    artifact["episode_status"] = "invalidated"
    artifact["invalidation_reason"] = reason
    artifact["event_log"] = list(artifact.get("event_log", [])) + [
        {"event": "episode_invalidated", "reason": reason, "recorded_at": utc_now()}
    ]
    atomic_write_json(artifact_path, artifact)
    audit_record = _append_capture_audit(paths, artifact, artifact_path, "invalidated")
    artifact["audit_ledger_record_id"] = audit_record["record_id"]
    atomic_write_json(artifact_path, artifact)
    return {"episode_id": episode_id, "episode_status": "invalidated", "artifact_path": str(artifact_path.resolve())}


def missed_capture(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    *,
    script: dict[str, Any] | None = None,
    collection_phase: str | None = None,
    input_func: Any = input,
) -> dict[str, Any]:
    script = script if script is not None else _prompt_missed(input_func)
    if collection_phase is not None:
        script = {**script, "collection_phase": collection_phase}
    paths = paths_for(data_dir)
    recover_atomic_writes(paths.root)
    missed_uuid = str(script.get("missed_uuid") or uuid4())
    missed_id = str(script.get("missed_id") or f"missed_c001_{missed_uuid.replace('-', '')[:16]}")
    occurred_at = _script_time(script, "occurred_at")
    artifact = {
        "collector_schema_version": CAPTURE_SCHEMA_VERSION,
        "campaign_id": CAMPAIGN_001_ID,
        "missed_id": missed_id,
        "missed_uuid": missed_uuid,
        "record_type": "missed_pre_decision_episode",
        "eligible_episode": bool(script.get("eligible_episode", True)),
        "eligibility_rule_version": str(script.get("eligibility_rule_version") or ELIGIBILITY_RULE_VERSION),
        "eligibility_rule": ELIGIBILITY_RULE_TEXT,
        "collection_phase": _collection_phase(script),
        "episode_status": "incomplete",
        "capture_status": "missed_pre_decision",
        "occurred_at": occurred_at,
        "timezone": str(script.get("timezone") or _local_timezone_name()),
        "task_description": str(script.get("task_description") or "unavailable"),
        "reason": str(script.get("reason") or "not captured before decision"),
        "protected_outcome": None,
        "source_hash": stable_hash(
            {
                "campaign_id": CAMPAIGN_001_ID,
                "missed_id": missed_id,
                "occurred_at": occurred_at,
                "task_description": str(script.get("task_description") or "unavailable"),
                "reason": str(script.get("reason") or "not captured before decision"),
            }
        ),
        "event_log": [{"event": "missed_pre_decision_recorded", "recorded_at": utc_now()}],
    }
    path = paths.missed / f"{missed_id}.json"
    atomic_write_json(path, artifact)
    audit_record = _append_missed_audit(paths, artifact, path)
    artifact["audit_ledger_record_id"] = audit_record["record_id"]
    atomic_write_json(path, artifact)
    return {
        "missed_id": missed_id,
        "artifact_path": str(path.resolve()),
        "audit_ledger_record_id": audit_record["record_id"],
        "eligible_episode": artifact["eligible_episode"],
        "capture_status": artifact["capture_status"],
    }


def status_capture(data_dir: str | Path = DEFAULT_DATA_DIR) -> dict[str, Any]:
    paths = paths_for(data_dir)
    recover_atomic_writes(paths.root)
    captures = _load_all_captures(paths)
    missed = _load_all_json(paths.missed)
    counts = {status: 0 for status in sorted(EPISODE_STATUSES)}
    pending_followups = 0
    overdue_followups = 0
    natural_completed = 0
    pilot_completed = 0
    for capture in captures:
        status = str(capture.get("episode_status") or "incomplete")
        if status in counts:
            counts[status] += 1
        collection_phase = _artifact_collection_phase(capture)
        if (
            status == "completed"
            and capture.get("sealed_pre_decision_snapshot", {}).get("provenance", {}).get("collection_mode")
            == "natural_observation"
            and collection_phase == "real"
        ):
            natural_completed += 1
        if status == "completed" and collection_phase == "pilot":
            pilot_completed += 1
        pending_followups += sum(1 for followup in capture.get("followups", []) if followup.get("status") == "pending")
        overdue_followups += _overdue_followup_count(capture)
    ledger_path = paths.ledger_dir / "ledger.jsonl"
    ledger_valid = True
    ledger_records = 0
    if ledger_path.exists():
        ledger = ImmutableLedger(ledger_path)
        ledger_valid = ledger.verify_hash_chain()
        ledger_records = len(ledger.scan())
        audit_records = len(ledger.scan(AUDIT_RECORD_TYPE))
    else:
        audit_records = 0
    return {
        "data_dir": str(paths.root.resolve()),
        "campaign_id": CAMPAIGN_001_ID,
        "capture_files": len(captures),
        "missed_eligible_episode_count": sum(1 for item in missed if item.get("eligible_episode") is True),
        "completed_natural_episode_count": natural_completed,
        "completed_pilot_episode_count": pilot_completed,
        "episode_status_counts": counts,
        "pending_followup_count": pending_followups,
        "overdue_followup_count": overdue_followups,
        "ledger_path": str(ledger_path.resolve()),
        "ledger_records": ledger_records,
        "audit_records": audit_records,
        "ledger_valid": ledger_valid,
    }


def validate_pre_decision_snapshot(
    snapshot: dict[str, Any],
    *,
    missing_fields: list[str] | None = None,
    feature_errors: list[str] | None = None,
) -> dict[str, Any]:
    errors = list(feature_errors or [])
    missing = list(missing_fields or [])
    if snapshot.get("schema_version") != BRIDGE_SCHEMA_VERSION:
        errors.append(f"schema_version must be {BRIDGE_SCHEMA_VERSION!r}")
    if snapshot.get("campaign_schema_version") != CAMPAIGN_001_SCHEMA_VERSION:
        errors.append(f"campaign_schema_version must be {CAMPAIGN_001_SCHEMA_VERSION!r}")
    if snapshot.get("campaign_id") != CAMPAIGN_001_ID:
        errors.append(f"campaign_id must be {CAMPAIGN_001_ID!r}")
    try:
        decision = parse_time(str(snapshot.get("decision_time", "")))
        cutoff = parse_time(str(snapshot.get("observation_cutoff", "")))
        if cutoff > decision:
            errors.append("observation_cutoff may not occur after decision_time")
    except (TypeError, ValueError) as exc:
        errors.append(str(exc))
    features = snapshot.get("pre_decision_features")
    if not isinstance(features, dict):
        errors.append("pre_decision_features must be an object")
    else:
        extra = sorted(set(features) - set(FEATURE_NAMES))
        if extra:
            errors.append(f"Unexpected pre-decision fields: {extra}")
        try:
            assert_feature_map_is_pre_decision(features, target_name=CAMPAIGN_001_TARGET)
        except ValueError as exc:
            errors.append(str(exc))
    actions = snapshot.get("available_actions")
    if not isinstance(actions, list) or not all(isinstance(action, str) and action.strip() for action in actions):
        errors.append("available_actions must be non-empty strings")
    elif len(set(actions)) != len(actions):
        errors.append("available_actions must be unique")
    provenance = snapshot.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("provenance must be an object")
    return {"valid": not errors and not missing, "errors": errors, "missing_fields": missing}


def import_export_idempotently(path: str | Path, *, data_dir: str | Path) -> dict[str, Any]:
    validation = validate_snapshot_file(path, campaign_id=CAMPAIGN_001_ID)
    snapshots = _load_jsonl(path)
    if len(snapshots) != 1:
        raise BridgeValidationError("Collector finalization expects exactly one snapshot per export")
    snapshot = snapshots[0]
    episode_id = str(snapshot["episode_id"])
    source_hash = str(snapshot["source_hash"])
    ledger = ImmutableLedger(Path(data_dir) / "ledger.jsonl")
    existing = ledger.find_record(episode_id, "decision_episode")
    if existing is not None:
        existing_hash = existing.get("payload", {}).get("data_provenance", {}).get("source_hash")
        if existing_hash != source_hash:
            raise BridgeValidationError(f"Existing ledger record {episode_id!r} has a different source_hash")
        ledger.verify_hash_chain()
        return {
            "imported": False,
            "ledger": str(ledger.path),
            "ledger_record_id": episode_id,
            "source_hash": source_hash,
            "snapshots": validation["snapshots"],
            "ledger_valid": True,
        }
    try:
        result: BridgeImportResult = import_snapshot_file(path, data_dir=data_dir, campaign_id=CAMPAIGN_001_ID)
    except DuplicateRecordError:
        existing = ledger.find_record(episode_id, "decision_episode")
        if existing is None:
            raise
        existing_hash = existing.get("payload", {}).get("data_provenance", {}).get("source_hash")
        if existing_hash != source_hash:
            raise BridgeValidationError(f"Existing ledger record {episode_id!r} has a different source_hash")
        result = BridgeImportResult(CAMPAIGN_001_ID, 0, str(ledger.path), False, [source_hash])
    ledger.verify_hash_chain()
    return {
        "imported": bool(result.imported),
        "ledger": result.ledger,
        "ledger_record_id": episode_id,
        "source_hash": source_hash,
        "snapshots": validation["snapshots"],
        "ledger_valid": True,
    }


def atomic_write_json(path: str | Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n")


def atomic_write_text(path: str | Path, text: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, destination)


def recover_atomic_writes(root: str | Path) -> int:
    base = Path(root)
    if not base.exists():
        return 0
    count = 0
    for tmp in base.rglob("*.tmp"):
        try:
            tmp.unlink()
            count += 1
        except FileNotFoundError:
            pass
    return count


def _collect_features(script: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str], list[str], list[str]]:
    raw_features = script.get("pre_decision_features")
    source = raw_features if isinstance(raw_features, dict) else script
    features: dict[str, Any] = {}
    source_statuses: dict[str, str] = {}
    missing: list[str] = []
    errors: list[str] = []
    has_deadline_value = source.get("has_deadline")
    has_deadline_missing = _is_missing(has_deadline_value)
    parsed_has_deadline: bool | None = None
    if has_deadline_missing:
        missing.append("has_deadline")
        features["has_deadline"] = None
        source_statuses["has_deadline"] = "unavailable"
    else:
        try:
            parsed_has_deadline = _coerce_bool(has_deadline_value, "has_deadline")
            features["has_deadline"] = parsed_has_deadline
            supplied_statuses = script.get("source_statuses", {})
            supplied_status = supplied_statuses.get("has_deadline") if isinstance(supplied_statuses, dict) else None
            source_statuses["has_deadline"] = _validate_field_source(supplied_status or "manual")
        except (TypeError, ValueError) as exc:
            features["has_deadline"] = has_deadline_value
            source_statuses["has_deadline"] = "manual"
            errors.append(str(exc))

    for name in FEATURE_NAMES:
        if name == "has_deadline":
            continue
        value = source.get(name)
        supplied_statuses = script.get("source_statuses", {})
        supplied_status = supplied_statuses.get(name) if isinstance(supplied_statuses, dict) else None
        if _is_missing(value):
            if name == "deadline_hours" and parsed_has_deadline is False:
                features[name] = None
                source_statuses[name] = _validate_field_source(supplied_status or "manual")
            else:
                features[name] = None
                source_statuses[name] = "unavailable"
                missing.append(name)
            continue
        try:
            if name == "deadline_hours" and parsed_has_deadline is False:
                raise ValueError("deadline_hours must be null when has_deadline is false")
            features[name] = _coerce_feature(name, value)
            source_statuses[name] = _validate_field_source(supplied_status or "manual")
        except (TypeError, ValueError) as exc:
            features[name] = value
            source_statuses[name] = _validate_field_source(supplied_status or "manual")
            errors.append(str(exc))
    return features, source_statuses, missing, errors


def _collect_outcomes(script: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    raw_outcome = script.get("protected_outcome")
    source = raw_outcome if isinstance(raw_outcome, dict) else script
    outcome: dict[str, Any] = {}
    sources: dict[str, str] = {}
    missing: list[str] = []
    raw_sources = script.get("outcome_sources", {})
    if raw_sources is None:
        raw_sources = {}
    if not isinstance(raw_sources, dict):
        raise CollectorError("outcome_sources must be an object")
    for name in OUTCOME_NAMES:
        status = _validate_outcome_source(raw_sources.get(name, "manual_observation"))
        if name not in source or _is_missing(source.get(name)) or status == "unavailable":
            outcome[name] = None
            sources[name] = "unavailable"
            missing.append(name)
            continue
        outcome[name] = _coerce_outcome(name, source[name])
        sources[name] = status
    if (
        "started_within_10_minutes" not in missing
        and "start_latency_seconds" not in missing
        and bool(outcome["started_within_10_minutes"]) != (int(outcome["start_latency_seconds"]) <= 600)
    ):
        raise CollectorError("started_within_10_minutes must match start_latency_seconds <= 600")
    return outcome, sources, missing


def _coerce_feature(name: str, value: Any) -> Any:
    if name in {"task_type", "time_of_day"}:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{name} must be a non-empty string")
        return text
    if name in {"first_step_explicit", "public_commitment", "has_deadline"}:
        return _coerce_bool(value, name)
    if name in {"fatigue", "ambiguity", "estimated_minutes", "recent_context_switches"}:
        number = _coerce_int(value, name)
        if name in {"fatigue", "ambiguity"} and not 0 <= number <= 3:
            raise ValueError(f"{name} must be in 0..3")
        if name == "estimated_minutes" and number <= 0:
            raise ValueError("estimated_minutes must be positive")
        if name == "recent_context_switches" and number < 0:
            raise ValueError("recent_context_switches must be non-negative")
        return number
    if name == "deadline_hours":
        number = float(value)
        if number < 0:
            raise ValueError("deadline_hours must be non-negative")
        return number
    raise ValueError(f"Unsupported Campaign 001 field: {name}")


def _coerce_outcome(name: str, value: Any) -> Any:
    if name in {"started_within_10_minutes", "worked_for_20_minutes", "completed_that_day"}:
        return _coerce_bool(value, name)
    if name == "start_latency_seconds":
        number = _coerce_int(value, name)
        if number < 0:
            raise ValueError("start_latency_seconds must be non-negative")
        return number
    raise ValueError(f"Unsupported Campaign 001 outcome: {name}")


def _coerce_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "t", "yes", "y", "1"}:
            return True
        if lowered in {"false", "f", "no", "n", "0"}:
            return False
    raise ValueError(f"{name} must be boolean")


def _coerce_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        return int(value)
    raise ValueError(f"{name} must be an integer")


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in {"", "na", "n/a", "null", "unavailable"})


def _validate_field_source(value: Any) -> str:
    text = str(value)
    if text not in FIELD_SOURCE_STATUSES:
        raise CollectorError(f"source status must be one of {sorted(FIELD_SOURCE_STATUSES)}")
    return text


def _validate_outcome_source(value: Any) -> str:
    text = str(value)
    if text not in OUTCOME_SOURCE_STATUSES:
        raise CollectorError(f"outcome source must be one of {sorted(OUTCOME_SOURCE_STATUSES)}")
    return text


def _reject_outcomes_in_start(script: dict[str, Any]) -> None:
    leaked = sorted(set(script) & set(OUTCOME_NAMES))
    if "protected_outcome" in script:
        leaked.append("protected_outcome")
    if leaked:
        raise CollectorError(f"Outcome fields are not accepted during pre-decision capture: {leaked}")


def _available_actions(script: dict[str, Any]) -> list[str]:
    raw = script.get("available_actions", DEFAULT_AVAILABLE_ACTIONS)
    if not isinstance(raw, list) or not all(isinstance(action, str) and action.strip() for action in raw):
        raise CollectorError("available_actions must be a non-empty list of strings")
    actions = [action.strip() for action in raw]
    if not actions or len(set(actions)) != len(actions):
        raise CollectorError("available_actions must be non-empty and unique")
    return actions


def _followups_for(decision_time: str) -> list[dict[str, Any]]:
    decision = parse_time(decision_time)
    end_of_day = decision.replace(hour=23, minute=59, second=0, microsecond=0)
    return [
        {
            "name": "t_plus_10",
            "due_at": (decision + timedelta(minutes=10)).isoformat(),
            "status": "pending",
            "fields": ["started_within_10_minutes", "start_latency_seconds"],
        },
        {
            "name": "t_plus_20",
            "due_at": (decision + timedelta(minutes=20)).isoformat(),
            "status": "pending",
            "fields": ["worked_for_20_minutes"],
        },
        {
            "name": "end_of_day",
            "due_at": end_of_day.isoformat(),
            "status": "pending",
            "fields": ["completed_that_day"],
        },
    ]


def _complete_followups(followups: list[dict[str, Any]], outcome_sources: dict[str, str]) -> list[dict[str, Any]]:
    completed = []
    for followup in followups:
        fields = list(followup.get("fields", []))
        completed.append(
            {
                **followup,
                "status": "completed"
                if all(outcome_sources.get(field) != "unavailable" for field in fields)
                else "missed",
            }
        )
    return completed


def _overdue_followup_count(capture: dict[str, Any]) -> int:
    if capture.get("episode_status") != "active":
        return 0
    now = datetime.now().astimezone()
    count = 0
    for followup in capture.get("followups", []):
        if followup.get("status") != "pending":
            continue
        try:
            due_at = parse_time(str(followup.get("due_at")))
        except ValueError:
            continue
        if due_at < now:
            count += 1
    return count


def _ledger_valid(data_dir: Path) -> bool:
    ledger_path = data_dir / "ledger.jsonl"
    if not ledger_path.exists():
        return True
    return ImmutableLedger(ledger_path).verify_hash_chain()


def _append_capture_audit(
    paths: CollectorPaths,
    artifact: dict[str, Any],
    artifact_path: Path,
    audit_event: str,
) -> dict[str, Any]:
    payload = {
        "audit_schema_version": AUDIT_SCHEMA_VERSION,
        "campaign_id": CAMPAIGN_001_ID,
        "campaign_schema_version": CAMPAIGN_001_SCHEMA_VERSION,
        "audit_event": audit_event,
        "record_kind": "captured_episode",
        "episode_id": artifact.get("episode_id"),
        "episode_status": artifact.get("episode_status"),
        "eligible_episode": artifact.get("eligible_episode"),
        "eligibility_rule_version": artifact.get("eligibility_rule_version"),
        "eligibility_rule": ELIGIBILITY_RULE_TEXT,
        "collection_phase": _artifact_collection_phase(artifact),
        "pre_decision_hash": artifact.get("pre_decision_hash"),
        "pre_decision_validation": artifact.get("pre_decision_validation"),
        "invalidation_reason": artifact.get("invalidation_reason"),
        "missing_outcome_fields": artifact.get("missing_outcome_fields", []),
        "sealed_pre_decision_snapshot": artifact.get("sealed_pre_decision_snapshot"),
        "protected_outcome": artifact.get("protected_outcome"),
        "artifact_hash": stable_hash({key: value for key, value in artifact.items() if key != "audit_ledger_record_id"}),
        "artifact_path": str(artifact_path.resolve()),
    }
    return _append_audit_payload(paths, payload)


def _append_missed_audit(paths: CollectorPaths, artifact: dict[str, Any], artifact_path: Path) -> dict[str, Any]:
    payload = {
        "audit_schema_version": AUDIT_SCHEMA_VERSION,
        "campaign_id": CAMPAIGN_001_ID,
        "campaign_schema_version": CAMPAIGN_001_SCHEMA_VERSION,
        "audit_event": "missed_eligible",
        "record_kind": "missed_pre_decision_episode",
        "missed_id": artifact.get("missed_id"),
        "episode_status": artifact.get("episode_status"),
        "eligible_episode": artifact.get("eligible_episode"),
        "eligibility_rule_version": artifact.get("eligibility_rule_version"),
        "eligibility_rule": ELIGIBILITY_RULE_TEXT,
        "collection_phase": artifact.get("collection_phase"),
        "occurred_at": artifact.get("occurred_at"),
        "task_description": artifact.get("task_description"),
        "reason": artifact.get("reason"),
        "source_hash": artifact.get("source_hash"),
        "artifact_hash": stable_hash({key: value for key, value in artifact.items() if key != "audit_ledger_record_id"}),
        "artifact_path": str(artifact_path.resolve()),
    }
    return _append_audit_payload(paths, payload)


def _append_audit_payload(paths: CollectorPaths, payload: dict[str, Any]) -> dict[str, Any]:
    ledger = ImmutableLedger(paths.ledger_dir / "ledger.jsonl")
    ensure_campaign_definition(ledger, CAMPAIGN_001_ID)
    body = dict(payload)
    audit_hash = stable_hash(body)
    body["audit_hash"] = audit_hash
    record_id = f"campaign_001_audit_{audit_hash[:16]}"
    existing = ledger.find_record(record_id, AUDIT_RECORD_TYPE)
    if existing is not None:
        if existing.get("payload") != body:
            raise CollectorError(f"Existing audit record {record_id!r} differs from current payload")
        ledger.verify_hash_chain()
        return {"record_id": record_id, "imported": False, "ledger": str(ledger.path), "ledger_valid": True}
    ledger.append(AUDIT_RECORD_TYPE, body, record_id=record_id, unique_record_id=True)
    ledger.verify_hash_chain()
    return {"record_id": record_id, "imported": True, "ledger": str(ledger.path), "ledger_valid": True}


def _collection_phase(script: dict[str, Any]) -> str:
    value = str(script.get("collection_phase") or "real").strip().lower()
    if value not in {"pilot", "real"}:
        raise CollectorError("collection_phase must be 'pilot' or 'real'")
    return value


def _artifact_collection_phase(artifact: dict[str, Any]) -> str:
    snapshot = artifact.get("sealed_pre_decision_snapshot", {})
    provenance = snapshot.get("provenance", {}) if isinstance(snapshot, dict) else {}
    return str(provenance.get("collection_phase") or "real")


def _script_time(script: dict[str, Any], key: str) -> str:
    value = script.get(key)
    if value is not None:
        parse_time(str(value))
        return str(value)
    return datetime.now().astimezone().isoformat()


def _local_timezone_name() -> str:
    local = datetime.now().astimezone()
    name = local.tzname()
    return name or str(local.tzinfo)


def _capture_path(paths: CollectorPaths, episode_id: str) -> Path:
    return paths.captures / f"{episode_id}.json"


def _load_capture(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CollectorError(f"Capture artifact not found: {path}")
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise CollectorError(f"Capture artifact must be a JSON object: {path}")
    return payload


def _load_all_captures(paths: CollectorPaths) -> list[dict[str, Any]]:
    return _load_all_json(paths.captures)


def _load_all_json(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    payloads = []
    for path in sorted(root.glob("*.json")):
        payload = _read_json(path)
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    snapshots = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            snapshots.append(json.loads(line))
    return snapshots


def _start_summary(artifact: dict[str, Any], artifact_path: Path, *, recovered: int, already_exists: bool) -> dict[str, Any]:
    return {
        "campaign_id": CAMPAIGN_001_ID,
        "episode_id": artifact["episode_id"],
        "episode_status": artifact["episode_status"],
        "artifact_path": str(artifact_path.resolve()),
        "pre_decision_hash": artifact["pre_decision_hash"],
        "pre_decision_valid": artifact.get("pre_decision_validation", {}).get("valid", False),
        "missing_fields": artifact.get("pre_decision_validation", {}).get("missing_fields", []),
        "recovered_temp_files": recovered,
        "already_exists": already_exists,
        "next_followups": artifact.get("followups", []),
    }


def _finalize_summary(artifact: dict[str, Any], artifact_path: Path, *, already_imported: bool) -> dict[str, Any]:
    bridge = artifact.get("bridge_import", {})
    return {
        "campaign_id": CAMPAIGN_001_ID,
        "episode_id": artifact["episode_id"],
        "episode_status": artifact["episode_status"],
        "artifact_path": str(artifact_path.resolve()),
        "bridge_export_path": artifact.get("bridge_export", {}).get("path"),
        "source_hash": artifact.get("source_hash"),
        "ledger_record_id": bridge.get("ledger_record_id"),
        "ledger_path": bridge.get("ledger"),
        "ledger_valid": bridge.get("ledger_valid", False),
        "imported": bridge.get("imported", False),
        "already_imported": already_imported,
    }


def _lookup_field(artifact: dict[str, Any], field: str) -> Any:
    if field in artifact:
        return artifact[field]
    snapshot = artifact.get("sealed_pre_decision_snapshot", {})
    features = snapshot.get("pre_decision_features", {}) if isinstance(snapshot, dict) else {}
    if field in features:
        return features[field]
    outcome = artifact.get("protected_outcome", {})
    if isinstance(outcome, dict) and field in outcome:
        return outcome[field]
    return None


def _prompt_start(input_func: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "task_description": input_func("Task description: ").strip(),
        "task_type": input_func("task_type: ").strip(),
        "time_of_day": input_func("time_of_day: ").strip(),
        "fatigue": input_func("fatigue 0-3: ").strip(),
        "ambiguity": input_func("ambiguity 0-3: ").strip(),
        "estimated_minutes": input_func("estimated_minutes: ").strip(),
        "first_step_explicit": input_func("first_step_explicit true/false: ").strip(),
        "has_deadline": input_func("has_deadline true/false: ").strip(),
        "deadline_hours": input_func("deadline_hours (blank only when has_deadline is false): ").strip(),
        "recent_context_switches": input_func("recent_context_switches: ").strip(),
        "public_commitment": input_func("public_commitment true/false: ").strip(),
        "manual_note": input_func("optional note (not a feature): ").strip() or None,
    }
    return values


def _prompt_outcome(input_func: Any) -> dict[str, Any]:
    return {
        "started_within_10_minutes": input_func("started_within_10_minutes true/false: ").strip(),
        "start_latency_seconds": input_func("start_latency_seconds: ").strip(),
        "worked_for_20_minutes": input_func("worked_for_20_minutes true/false: ").strip(),
        "completed_that_day": input_func("completed_that_day true/false: ").strip(),
    }


def _prompt_missed(input_func: Any) -> dict[str, Any]:
    return {
        "task_description": input_func("Missed task description: ").strip(),
        "reason": input_func("Reason not captured before decision: ").strip(),
    }
