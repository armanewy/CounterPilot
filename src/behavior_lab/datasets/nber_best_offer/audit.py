from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from typing import Any

from behavior_lab.benchmarks.metrics import classification_accuracy, multiclass_log_loss, regression_rmse
from behavior_lab.benchmarks.splits import assert_disjoint_groups, chronological_group_purged_split, group_disjoint_split
from behavior_lab.core import stable_hash
from behavior_lab.datasets.nber_best_offer.baselines import CategoryMajorityClassifier, MajorityClassifier, MedianRegressor, OfferRatioThresholdClassifier
from behavior_lab.datasets.nber_best_offer.tasks import assert_no_future_leakage, build_tasks
from behavior_lab.offerlab_models.common import validate_feature_contract


@dataclass(frozen=True)
class NberAuditReport:
    dataset_dir: str
    tasks: dict[str, Any]
    leakage_checks: dict[str, bool]
    split_checks: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def benchmark(normalized_dir: str | Path) -> dict[str, Any]:
    tasks = build_tasks(normalized_dir)
    leaderboards: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for task_name, rows in tasks.items():
        if not rows:
            leaderboards[task_name] = {"chronological": [], "seller_disjoint": []}
            continue
        leaderboards[task_name] = {
            "chronological": _evaluate_split(task_name, chronological_group_purged_split(rows, time_key="timestamp", group_key="listing_id"), split_type="chronological"),
            "seller_disjoint": _evaluate_split(task_name, group_disjoint_split(rows, group_key="seller_id"), split_type="seller_disjoint"),
        }
    return {"scope": _benchmark_scope(normalized_dir), "leaderboards": leaderboards}


def audit(normalized_dir: str | Path, *, output_path: str | Path | None = None) -> dict[str, Any]:
    tasks = build_tasks(normalized_dir)
    leakage_checks = {
        task_name: assert_no_future_leakage(rows) and validate_feature_contract(rows)
        for task_name, rows in tasks.items()
    }
    split_checks = {}
    split_details = {}
    for task_name, rows in tasks.items():
        group_split = group_disjoint_split(rows, group_key="seller_id") if rows else None
        split_checks[f"{task_name}_seller_disjoint"] = assert_disjoint_groups(group_split, group_key="seller_id") if group_split else True
        chrono_split = chronological_group_purged_split(rows, time_key="timestamp", group_key="listing_id") if rows else None
        split_checks[f"{task_name}_listing_disjoint"] = assert_disjoint_groups(chrono_split, group_key="listing_id") if chrono_split else True
        split_details[task_name] = {
            "chronological": chrono_split.sizes() if chrono_split else {"train": 0, "development": 0, "hidden": 0},
            "chronological_group_key": "listing_id",
            "chronological_purge": {
                "purged_group_ids": list(chrono_split.purged_group_ids),
                "purged_rows": chrono_split.purged_rows,
            } if chrono_split else {"purged_group_ids": [], "purged_rows": 0},
            "seller_disjoint": group_split.sizes() if group_split else {"train": 0, "development": 0, "hidden": 0},
        }
    report = NberAuditReport(
        dataset_dir=_redacted_dataset_ref(normalized_dir),
        tasks={task_name: {"rows": len(rows), "splits": split_details[task_name]} for task_name, rows in tasks.items()},
        leakage_checks=leakage_checks,
        split_checks=split_checks,
    ).to_dict()
    if output_path is not None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _evaluate_split(task_name: str, split: Any, *, split_type: str) -> list[dict[str, Any]]:
    evaluation_rows = split.development
    split_name = "development"
    if not evaluation_rows:
        evaluation_rows = []
    if task_name in {"final_price_ratio", "response_latency"}:
        model = MedianRegressor().fit(split.train)
        predictions = model.predict(evaluation_rows).predictions
        return [
            {
                "model_id": model.model_id,
                "split_type": split_type,
                "split": split_name,
                "hidden_rows_reserved": len(split.hidden),
                "rmse": regression_rmse(predictions),
                "features_used": [],
            }
        ]
    models = [MajorityClassifier().fit(split.train), CategoryMajorityClassifier().fit(split.train)]
    if task_name == "seller_next_action":
        models.append(OfferRatioThresholdClassifier().fit(split.train))
    rows_out = []
    for model in models:
        result = model.predict(evaluation_rows)
        rows_out.append(
            {
                "model_id": result.model_id,
                "split_type": split_type,
                "split": split_name,
                "hidden_rows_reserved": len(split.hidden),
                "accuracy": classification_accuracy(result.predictions),
                "log_loss": multiclass_log_loss(result.predictions),
                "features_used": result.features_used,
            }
        )
    return rows_out


def _benchmark_scope(normalized_dir: str | Path) -> dict[str, Any]:
    root = Path(normalized_dir)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        return {
            "source_dataset_ids": ["nber_ebay_best_offer"],
            "research_only": True,
            "production_export_allowed": False,
            "full_release_evidence": False,
            "evidence_scope": "synthetic_fixture_or_legacy_normalized_smoke",
        }
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    args = manifest.get("command_args", {})
    full = bool(args.get("full"))
    limit_threads = args.get("limit_threads")
    full_gate = manifest.get("audited_full_release_evidence", {})
    full_gate_passed = _audited_full_release_gate_passed(full_gate)
    full_release_evidence = bool(full and limit_threads is None and full_gate_passed)
    return {
        "source_dataset_ids": manifest.get("source_dataset_ids", ["nber_ebay_best_offer"]),
        "research_only": bool(manifest.get("research_only", True)),
        "production_export_allowed": bool(manifest.get("production_export_allowed", False)),
        "commercial_training_allowed": bool(manifest.get("commercial_training_allowed", False)),
        "full_release_evidence": full_release_evidence,
        "full_release_gate_passed": full_gate_passed,
        "limit_threads": limit_threads,
        "evidence_scope": "full_release" if full_release_evidence else "bounded_smoke_or_semantics",
        "hidden_evaluation_policy": "development leaderboards only; hidden rows reserved for explicit one-shot benchmark protocol",
    }


def _audited_full_release_gate_passed(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = [
        "passed",
        "replication_contract_passed",
        "streaming_full_run_passed",
        "full_run_checkpoint_validated",
        "independent_audit_passed",
    ]
    return all(value.get(field) is True for field in required)


def _redacted_dataset_ref(normalized_dir: str | Path) -> str:
    resolved = Path(normalized_dir).resolve()
    data_root = Path(os.environ.get("OFFERLAB_DATA_ROOT", r"C:\OfferLabData")).resolve()
    try:
        relative = resolved.relative_to(data_root)
    except ValueError:
        return f"local_normalized_dir_hash:{stable_hash(str(resolved))[:16]}"
    return "$OFFERLAB_DATA_ROOT/" + relative.as_posix()
