from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from behavior_lab.core import stable_hash
from behavior_lab.data_sources.registry import default_registry


class BenchmarkContractError(ValueError):
    pass


@dataclass(frozen=True)
class ArtifactLineage:
    artifact_id: str
    source_dataset_ids: list[str]
    transformation_ids: list[str]
    allowed_uses: dict[str, bool]
    license_status: str
    dataset_hashes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkManifest:
    benchmark_id: str
    source_dataset_ids: list[str]
    task_type: str
    target_name: str
    feature_contract: list[str]
    forbidden_features: list[str]
    split_contract: dict[str, Any]
    lineage: ArtifactLineage

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def manifest_hash(self) -> str:
        return stable_hash(self.to_dict())


@dataclass(frozen=True)
class PredictionRecord:
    row_id: str
    label: Any
    prediction: Any
    split: str
    weight: float = 1.0
    group_id: str | None = None
    timestamp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_manifest(manifest: BenchmarkManifest | dict[str, Any]) -> dict[str, Any]:
    payload = manifest.to_dict() if isinstance(manifest, BenchmarkManifest) else dict(manifest)
    required = {
        "benchmark_id",
        "source_dataset_ids",
        "task_type",
        "target_name",
        "feature_contract",
        "forbidden_features",
        "split_contract",
        "lineage",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise BenchmarkContractError(f"Missing benchmark manifest fields: {missing}")
    overlap = set(payload["feature_contract"]) & set(payload["forbidden_features"])
    if overlap:
        raise BenchmarkContractError(f"Feature contract includes forbidden features: {sorted(overlap)}")
    if not payload["source_dataset_ids"]:
        raise BenchmarkContractError("source_dataset_ids may not be empty")
    lineage = payload["lineage"]
    if not isinstance(lineage, dict):
        raise BenchmarkContractError("lineage must be an object")
    lineage_sources = lineage.get("source_dataset_ids")
    if not isinstance(lineage_sources, list) or not all(isinstance(item, str) for item in lineage_sources):
        raise BenchmarkContractError("lineage.source_dataset_ids must be a list of strings")
    if not lineage_sources:
        raise BenchmarkContractError("lineage.source_dataset_ids may not be empty")
    if set(lineage_sources) != set(payload["source_dataset_ids"]):
        raise BenchmarkContractError("lineage.source_dataset_ids must match top-level source_dataset_ids")
    lineage_allowed_uses = lineage.get("allowed_uses")
    if not isinstance(lineage_allowed_uses, dict):
        raise BenchmarkContractError("lineage.allowed_uses must be an object")
    registry = default_registry()
    for use, claimed_allowed in lineage_allowed_uses.items():
        if claimed_allowed and not registry.verify_lineage(lineage_sources, str(use))["allowed"]:
            raise BenchmarkContractError(f"lineage.allowed_uses claims disallowed use {use!r}")
    permissions = registry.verify_lineage(list(payload["source_dataset_ids"]), "production_export")
    return {
        "valid": True,
        "benchmark_id": payload["benchmark_id"],
        "source_dataset_ids": payload["source_dataset_ids"],
        "manifest_hash": stable_hash(payload),
        "production_export_permission": permissions,
    }


def validate_manifest_file(path: str | Path) -> dict[str, Any]:
    return validate_manifest(json.loads(Path(path).read_text(encoding="utf-8")))
