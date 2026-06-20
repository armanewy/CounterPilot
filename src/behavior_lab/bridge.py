from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from behavior_lab.core import DecisionEpisode, parse_time, stable_hash, utc_now
from behavior_lab.ledger import ImmutableLedger
from behavior_lab.temporal import assert_feature_map_is_pre_decision


BRIDGE_SCHEMA_VERSION = "behavior_lab_campaign_snapshot.v1"
CAMPAIGN_001_ID = "campaign_001_task_initiation"
CAMPAIGN_001_TARGET = "started_within_10_minutes"

CAMPAIGN_001_FEATURES: dict[str, type] = {
    "task_type": str,
    "time_of_day": str,
    "fatigue": int,
    "ambiguity": int,
    "estimated_minutes": int,
    "first_step_explicit": bool,
    "deadline_hours": float,
    "recent_context_switches": int,
    "public_commitment": bool,
}

CAMPAIGN_001_OUTCOMES: dict[str, type] = {
    "started_within_10_minutes": bool,
    "start_latency_seconds": int,
    "worked_for_20_minutes": bool,
    "completed_that_day": bool,
}


class BridgeValidationError(ValueError):
    pass


@dataclass(frozen=True)
class BridgeImportResult:
    campaign_id: str
    imported: int
    ledger: str
    campaign_definition_recorded: bool
    source_hashes: list[str]


def campaign_001_definition() -> dict[str, Any]:
    return {
        "campaign_id": CAMPAIGN_001_ID,
        "title": "Campaign 001 - task initiation",
        "target": {
            "name": CAMPAIGN_001_TARGET,
            "question": "Did I begin the intended task within 10 minutes?",
            "type": "binary",
        },
        "collection_plan": {
            "initial_block": "50 natural episodes",
            "interventions": "none during initial observational block",
            "manual_entry_ok": True,
        },
        "pre_decision_features": {
            "task_type": "string",
            "time_of_day": "string",
            "fatigue": "integer 0..3",
            "ambiguity": "integer 0..3",
            "estimated_minutes": "positive integer",
            "first_step_explicit": "boolean",
            "deadline_hours": "non-negative number",
            "recent_context_switches": "non-negative integer",
            "public_commitment": "boolean",
        },
        "protected_outcome": {
            "started_within_10_minutes": "boolean",
            "start_latency_seconds": "non-negative integer",
            "worked_for_20_minutes": "boolean",
            "completed_that_day": "boolean",
        },
        "bridge_schema_version": BRIDGE_SCHEMA_VERSION,
    }


def source_hash_for_snapshot(snapshot: dict[str, Any]) -> str:
    body = dict(snapshot)
    body.pop("source_hash", None)
    return stable_hash(body)


def with_source_hash(snapshot: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(snapshot)
    prepared["source_hash"] = source_hash_for_snapshot(prepared)
    return prepared


def load_snapshots(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if not text.strip():
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if payload is None and source.suffix.lower() == ".jsonl":
        return _load_jsonl(text)
    if isinstance(payload, list):
        if not all(isinstance(item, dict) for item in payload):
            raise BridgeValidationError("Snapshot array entries must be JSON objects")
        return list(payload)
    if isinstance(payload, dict) and isinstance(payload.get("snapshots"), list):
        snapshots = payload["snapshots"]
        if not all(isinstance(item, dict) for item in snapshots):
            raise BridgeValidationError("snapshots entries must be JSON objects")
        return list(snapshots)
    if isinstance(payload, dict):
        return [payload]
    if payload is None:
        raise BridgeValidationError("Expected a JSON object, JSON array, or JSONL file")
    raise BridgeValidationError("Expected a JSON object, JSON array, or JSONL file")


def _load_jsonl(text: str) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BridgeValidationError(f"Invalid JSONL at line {line_number}") from exc
        if not isinstance(payload, dict):
            raise BridgeValidationError(f"Snapshot at line {line_number} must be a JSON object")
        snapshots.append(payload)
    return snapshots


def write_snapshots_jsonl(snapshots: list[dict[str, Any]], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        for snapshot in snapshots:
            handle.write(json.dumps(snapshot, sort_keys=True, ensure_ascii=True) + "\n")


def prepare_snapshot_file(input_path: str | Path, output_path: str | Path) -> list[dict[str, Any]]:
    snapshots = [with_source_hash(snapshot) for snapshot in load_snapshots(input_path)]
    write_snapshots_jsonl(snapshots, output_path)
    return snapshots


def validate_snapshot(snapshot: dict[str, Any], *, campaign_id: str | None = None) -> dict[str, Any]:
    if snapshot.get("schema_version") != BRIDGE_SCHEMA_VERSION:
        raise BridgeValidationError(f"schema_version must be {BRIDGE_SCHEMA_VERSION!r}")
    observed_campaign = snapshot.get("campaign_id")
    if campaign_id is not None and observed_campaign != campaign_id:
        raise BridgeValidationError(f"Expected campaign_id {campaign_id!r}, found {observed_campaign!r}")
    if observed_campaign != CAMPAIGN_001_ID:
        raise BridgeValidationError(f"Unsupported campaign_id {observed_campaign!r}")

    expected_hash = source_hash_for_snapshot(snapshot)
    if snapshot.get("source_hash") != expected_hash:
        raise BridgeValidationError("source_hash does not match the canonical snapshot body")

    decision_time = str(snapshot.get("decision_time", ""))
    observation_cutoff = str(snapshot.get("observation_cutoff", ""))
    parse_time(decision_time)
    parse_time(observation_cutoff)

    features = snapshot.get("pre_decision_features")
    if not isinstance(features, dict):
        raise BridgeValidationError("pre_decision_features must be an object")
    _validate_campaign_001_features(features)
    assert_feature_map_is_pre_decision(features, target_name=CAMPAIGN_001_TARGET)

    outcome = snapshot.get("protected_outcome")
    if not isinstance(outcome, dict):
        raise BridgeValidationError("protected_outcome must be an object")
    _validate_campaign_001_outcome(outcome)

    available_actions = snapshot.get("available_actions")
    if not isinstance(available_actions, list) or not all(isinstance(action, str) for action in available_actions):
        raise BridgeValidationError("available_actions must be a list of strings")
    if not available_actions:
        raise BridgeValidationError("available_actions may not be empty")

    provenance = snapshot.get("provenance")
    if not isinstance(provenance, dict):
        raise BridgeValidationError("provenance must be an object")
    return snapshot


def snapshot_to_decision_episode(snapshot: dict[str, Any]) -> DecisionEpisode:
    validated = validate_snapshot(snapshot)
    source_hash = str(validated["source_hash"])
    episode_id = str(validated.get("episode_id") or f"e_bl_{source_hash[:16]}")
    task_type = str(validated["pre_decision_features"]["task_type"])
    return DecisionEpisode(
        episode_id=episode_id,
        subject_id=str(validated.get("subject_id") or "arman"),
        decision_time=str(validated["decision_time"]),
        observation_cutoff=str(validated["observation_cutoff"]),
        situation={
            "type": "task_initiation",
            "campaign_id": validated["campaign_id"],
            "task_type": task_type,
            "description": str(validated.get("task_description") or task_type),
        },
        available_actions=list(validated["available_actions"]),
        pre_decision_context=dict(validated["pre_decision_features"]),
        observed_action=None,
        later_outcomes=dict(validated["protected_outcome"]),
        data_provenance={
            "source": "behavior_lab_bridge",
            "schema_version": validated["schema_version"],
            "campaign_id": validated["campaign_id"],
            "source_hash": source_hash,
            "provenance": dict(validated["provenance"]),
        },
    )


def validate_snapshot_file(path: str | Path, *, campaign_id: str | None = None) -> dict[str, Any]:
    snapshots = load_snapshots(path)
    hashes = []
    for snapshot in snapshots:
        validate_snapshot(snapshot, campaign_id=campaign_id)
        hashes.append(str(snapshot["source_hash"]))
    if len(set(hashes)) != len(hashes):
        raise BridgeValidationError("Duplicate source_hash values in export file")
    return {"snapshots": len(snapshots), "source_hashes": hashes}


def import_snapshot_file(
    path: str | Path,
    *,
    data_dir: str | Path,
    campaign_id: str = CAMPAIGN_001_ID,
) -> BridgeImportResult:
    snapshots = load_snapshots(path)
    if not snapshots:
        raise BridgeValidationError("No snapshots to import")
    validate_snapshot_file(path, campaign_id=campaign_id)
    ledger = ImmutableLedger(Path(data_dir) / "ledger.jsonl")
    campaign_recorded = _ensure_campaign_definition(ledger, campaign_id)
    episodes = [snapshot_to_decision_episode(snapshot) for snapshot in snapshots]
    entries = [
        ("decision_episode", episode, episode.episode_id)
        for episode in episodes
    ]
    ledger.append_many_guarded(entries, unique_record_ids=True)
    ledger.verify_hash_chain()
    return BridgeImportResult(
        campaign_id=campaign_id,
        imported=len(episodes),
        ledger=str(ledger.path),
        campaign_definition_recorded=campaign_recorded,
        source_hashes=[str(snapshot["source_hash"]) for snapshot in snapshots],
    )


def _ensure_campaign_definition(ledger: ImmutableLedger, campaign_id: str) -> bool:
    record_id = f"campaign_definition_{campaign_id}"
    existing = ledger.find_record(record_id, "campaign_definition")
    definition = campaign_001_definition()
    if existing is not None:
        if existing.get("payload") != definition:
            raise BridgeValidationError(f"Existing campaign definition {record_id!r} differs from current definition")
        return False
    ledger.append("campaign_definition", definition, record_id=record_id, unique_record_id=True)
    return True


def _validate_campaign_001_features(features: dict[str, Any]) -> None:
    missing = sorted(set(CAMPAIGN_001_FEATURES) - set(features))
    extra = sorted(set(features) - set(CAMPAIGN_001_FEATURES))
    if missing:
        raise BridgeValidationError(f"Missing pre-decision fields: {missing}")
    if extra:
        raise BridgeValidationError(f"Unexpected pre-decision fields: {extra}")
    for name, expected_type in CAMPAIGN_001_FEATURES.items():
        value = features[name]
        if expected_type is bool:
            if not isinstance(value, bool):
                raise BridgeValidationError(f"{name} must be boolean")
            continue
        if expected_type is int:
            if not isinstance(value, int) or isinstance(value, bool):
                raise BridgeValidationError(f"{name} must be an integer")
            continue
        if expected_type is float:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise BridgeValidationError(f"{name} must be a number")
            continue
        if expected_type is str and (not isinstance(value, str) or not value.strip()):
            raise BridgeValidationError(f"{name} must be a non-empty string")
    if not 0 <= int(features["fatigue"]) <= 3:
        raise BridgeValidationError("fatigue must be in 0..3")
    if not 0 <= int(features["ambiguity"]) <= 3:
        raise BridgeValidationError("ambiguity must be in 0..3")
    if int(features["estimated_minutes"]) <= 0:
        raise BridgeValidationError("estimated_minutes must be positive")
    if float(features["deadline_hours"]) < 0:
        raise BridgeValidationError("deadline_hours must be non-negative")
    if int(features["recent_context_switches"]) < 0:
        raise BridgeValidationError("recent_context_switches must be non-negative")


def _validate_campaign_001_outcome(outcome: dict[str, Any]) -> None:
    missing = sorted(set(CAMPAIGN_001_OUTCOMES) - set(outcome))
    extra = sorted(set(outcome) - set(CAMPAIGN_001_OUTCOMES))
    if missing:
        raise BridgeValidationError(f"Missing protected outcome fields: {missing}")
    if extra:
        raise BridgeValidationError(f"Unexpected protected outcome fields: {extra}")
    for name, expected_type in CAMPAIGN_001_OUTCOMES.items():
        value = outcome[name]
        if expected_type is bool:
            if not isinstance(value, bool):
                raise BridgeValidationError(f"{name} must be boolean")
        elif expected_type is int:
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise BridgeValidationError(f"{name} must be a non-negative integer")


def campaign_001_raw_template() -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "campaign_id": CAMPAIGN_001_ID,
        "episode_id": "replace-with-stable-id",
        "subject_id": "arman",
        "decision_time": now,
        "observation_cutoff": now,
        "task_description": "replace with the intended task",
        "available_actions": ["start_now", "defer", "switch_task", "abandon"],
        "pre_decision_features": {
            "task_type": "coding",
            "time_of_day": "morning",
            "fatigue": 1,
            "ambiguity": 1,
            "estimated_minutes": 45,
            "first_step_explicit": False,
            "deadline_hours": 24,
            "recent_context_switches": 0,
            "public_commitment": False,
        },
        "protected_outcome": {
            "started_within_10_minutes": False,
            "start_latency_seconds": 600,
            "worked_for_20_minutes": False,
            "completed_that_day": False,
        },
        "provenance": {
            "entry_method": "manual",
            "notes": "replace or remove notes before hashing if desired",
        },
    }


def write_campaign_001_template(path: str | Path) -> dict[str, Any]:
    template = campaign_001_raw_template()
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(template, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return template
