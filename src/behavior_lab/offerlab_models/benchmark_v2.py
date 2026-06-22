from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from typing import Any, Iterator

from behavior_lab import __version__
from behavior_lab.core import stable_hash, utc_now
from behavior_lab.datasets.nber_best_offer.real_normalize import sha256_file, verify_full_release_evidence
from behavior_lab.datasets.nber_best_offer.tasks import agreement_label
from behavior_lab.offerlab_models.benchmark_v2_protocol import validate_v2_hidden_exclusion
from behavior_lab.offerlab_models.common import FEATURE_CONTRACT, FORBIDDEN_MODEL_FIELDS, validate_feature_contract


DEFAULT_PROTOCOL = Path("datasets/manifests/offerlab_benchmark_v2.yaml")
DEFAULT_V1_FINAL = Path("reports/offerlab_benchmark_v1_final_manifest.json")
TARGETS = (
    "seller_next_action",
    "buyer_response_to_counter",
    "agreement",
    "final_price_ratio",
    "response_latency",
)
CLASSIFICATION_TARGETS = {"seller_next_action", "buyer_response_to_counter", "agreement"}
SPLITS = (
    "chronological_listing_purged",
    "seller_disjoint",
    "buyer_disjoint",
    "category_disjoint_diagnostic",
    "thread_safe_nested_development",
)


class BenchmarkV2Error(ValueError):
    pass


@dataclass(frozen=True)
class BenchmarkV2Paths:
    normalized_dir: Path
    output_dir: Path
    protocol_path: Path = DEFAULT_PROTOCOL
    v1_final_manifest_path: Path = DEFAULT_V1_FINAL
    external_v1_hidden_tokens_path: Path | None = None


def build_offerlab_benchmark_v2(
    paths: BenchmarkV2Paths,
    *,
    require_full_release: bool = True,
    partition_rows: int = 50_000,
) -> dict[str, Any]:
    """Build Benchmark v2 task files and immutable split manifests.

    The implementation streams normalized partitions and uses SQLite as the
    working index. It intentionally does not train models or call any model
    suite.
    """

    if partition_rows <= 0:
        raise ValueError("partition_rows must be positive")
    normalized_dir = Path(paths.normalized_dir)
    output_dir = Path(paths.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = _read_json(normalized_dir / "manifest.json")
    protocol = _read_json(paths.protocol_path)
    v1_final = _read_json(paths.v1_final_manifest_path)
    if require_full_release:
        evidence = verify_full_release_evidence(manifest)
        if not evidence["passed"]:
            raise BenchmarkV2Error(
                "Benchmark v2 requires audited full-release normalized input; "
                f"failures={evidence['failures']}"
            )

    db_path = output_dir / "benchmark_v2.sqlite"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    try:
        _init_db(conn)
        _index_listings(conn, normalized_dir, manifest)
        task_counts = _construct_cases(conn, normalized_dir, manifest)
        _write_public_task_files(conn, output_dir, partition_rows=partition_rows)
        split_reports = _write_split_manifests(conn, output_dir, protocol)
        hidden_report = _write_fresh_hidden_lockbox(
            conn,
            output_dir,
            protocol=protocol,
            v1_final_manifest=v1_final,
            external_v1_hidden_tokens=_load_external_v1_hidden_tokens(paths.external_v1_hidden_tokens_path),
            partition_rows=partition_rows,
        )
    finally:
        conn.close()

    report = {
        "schema_version": "offerlab_benchmark_v2_build.v1",
        "benchmark_id": "offerlab_benchmark_v2",
        "generated_at": utc_now(),
        "software_version": __version__,
        "git_commit": _git_commit(),
        "research_only": True,
        "production_export_allowed": False,
        "model_training_executed": False,
        "source_dataset_ids": ["nber_ebay_best_offer"],
        "normalization": {
            "manifest_hash": stable_hash(manifest),
            "normalization_manifest_hash": manifest.get("lineage", {}).get("normalization_manifest_hash"),
            "tables": {
                name: {"rows": table.get("rows"), "format": table.get("format")}
                for name, table in manifest.get("tables", {}).items()
            },
            "full_release_required": require_full_release,
        },
        "protocol": {
            "path": str(Path(paths.protocol_path).as_posix()),
            "hash": stable_hash(protocol),
            "targets": list(protocol.get("targets", TARGETS)),
        },
        "feature_contract": {
            "allowed_features": list(FEATURE_CONTRACT),
            "forbidden_features": sorted(FORBIDDEN_MODEL_FIELDS),
            "pre_decision_only": True,
        },
        "task_manifests": task_counts,
        "splits": split_reports,
        "fresh_hidden_lockbox": hidden_report,
        "ordinary_task_reader_policy": {
            "hidden_labels_returned": False,
            "labels_are_stored_separately": True,
        },
    }
    report["manifest_hash"] = stable_hash(report)
    manifest_path = output_dir / "manifest.json"
    _write_atomic_json(manifest_path, report)
    (output_dir / "manifest.json.sha256").write_text(f"{sha256_file(manifest_path)}  manifest.json\n", encoding="utf-8")
    return report


def read_v2_task_rows(
    benchmark_dir: str | Path,
    target: str,
    *,
    split_manifest: str | None = None,
    split_region: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Ordinary task reader.

    It never returns labels for hidden rows. Non-hidden rows are still public
    cases without labels; model-training code must use a dedicated protected
    label reader introduced by a later training wave.
    """

    root = Path(benchmark_dir)
    allowed_ids: set[str] | None = None
    hidden_ids: set[str] = set()
    if split_manifest is not None:
        payload = _read_json(root / "splits" / split_manifest / f"{target}.json")
        assignments = payload.get("assignments", {})
        hidden_ids = set(assignments.get("hidden", []))
        if split_region is not None:
            allowed_ids = set(assignments.get(split_region, []))
    task_dir = root / "tasks" / target
    for path in sorted(task_dir.glob("part-*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                row_id = str(row.get("row_id", ""))
                if allowed_ids is not None and row_id not in allowed_ids:
                    continue
                public = dict(row)
                public.pop("label", None)
                if row_id in hidden_ids:
                    public["label_redacted"] = True
                yield public


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE listings (
            listing_id TEXT PRIMARY KEY,
            seller_id TEXT,
            category TEXT,
            payload TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE cases (
            row_id TEXT PRIMARY KEY,
            target TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            listing_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            seller_id TEXT,
            buyer_id TEXT,
            category TEXT,
            case_token TEXT NOT NULL,
            outcome_state TEXT NOT NULL,
            public_json TEXT NOT NULL,
            label_json TEXT
        )
        """
    )
    conn.execute("CREATE INDEX cases_target_time ON cases(target, timestamp, row_id)")
    conn.execute("CREATE INDEX cases_target_listing ON cases(target, listing_id)")
    conn.execute("CREATE INDEX cases_target_seller ON cases(target, seller_id)")
    conn.execute("CREATE INDEX cases_target_buyer ON cases(target, buyer_id)")
    conn.execute("CREATE INDEX cases_target_category ON cases(target, category)")
    conn.execute("CREATE INDEX cases_target_thread ON cases(target, thread_id)")


def _index_listings(conn: sqlite3.Connection, normalized_dir: Path, manifest: dict[str, Any]) -> None:
    batch = []
    for listing in _iter_partitioned_table(normalized_dir, manifest, "listings"):
        batch.append(
            (
                _canonical_identifier(listing.get("listing_id")),
                _canonical_identifier(listing.get("seller_id")),
                str(listing.get("category") or ""),
                json.dumps(listing, sort_keys=True),
            )
        )
        if len(batch) >= 10_000:
            conn.executemany("INSERT OR REPLACE INTO listings VALUES (?, ?, ?, ?)", batch)
            conn.commit()
            batch = []
    if batch:
        conn.executemany("INSERT OR REPLACE INTO listings VALUES (?, ?, ?, ?)", batch)
        conn.commit()


def _construct_cases(conn: sqlite3.Connection, normalized_dir: Path, manifest: dict[str, Any]) -> dict[str, dict[str, int | bool]]:
    counts = {
        target: {
            "eligible_rows": 0,
            "supervised_rows": 0,
            "unknown_outcome_rows": 0,
            "censored_outcome_rows": 0,
            "excluded_rows": 0,
            "unknown_and_censored_labeled_as_rejection": False,
        }
        for target in TARGETS
    }
    current_thread: list[dict[str, Any]] = []
    current_thread_id: str | None = None
    batch: list[tuple[Any, ...]] = []

    for turn in _iter_partitioned_table(normalized_dir, manifest, "negotiation_turns"):
        thread_id = str(turn.get("thread_id") or "")
        if current_thread and thread_id != current_thread_id:
            batch.extend(_case_records_for_thread(conn, current_thread, counts))
            if len(batch) >= 10_000:
                _insert_case_batch(conn, batch)
                batch = []
            current_thread = []
        current_thread.append(turn)
        current_thread_id = thread_id
    if current_thread:
        batch.extend(_case_records_for_thread(conn, current_thread, counts))
    if batch:
        _insert_case_batch(conn, batch)
    conn.commit()
    return counts


def _case_records_for_thread(
    conn: sqlite3.Connection,
    turns: list[dict[str, Any]],
    counts: dict[str, dict[str, int | bool]],
) -> list[tuple[Any, ...]]:
    if not turns:
        return []
    turns = sorted(turns, key=lambda row: int(row.get("turn_index") or 0))
    listing_id = _canonical_identifier(turns[0].get("listing_id"))
    listing_payload = conn.execute("SELECT payload FROM listings WHERE listing_id = ?", (listing_id,)).fetchone()
    if listing_payload is None:
        for target in TARGETS:
            counts[target]["excluded_rows"] = int(counts[target]["excluded_rows"]) + 1
        return []
    listing = json.loads(listing_payload[0])
    records: list[tuple[Any, ...]] = []
    for case in _seller_next_action_cases(listing, turns):
        records.append(_case_record(case, counts))
    for case in _buyer_response_to_counter_cases(listing, turns):
        records.append(_case_record(case, counts))
    records.append(_case_record(_agreement_case(listing, turns), counts))
    records.append(_case_record(_final_price_ratio_case(listing, turns), counts))
    for case in _response_latency_cases(listing, turns):
        records.append(_case_record(case, counts))
    return records


def _seller_next_action_cases(listing: dict[str, Any], turns: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for index, turn in enumerate(turns):
        if turn.get("actor") != "buyer" or turn.get("action") not in {"offer", "counter"}:
            continue
        next_turn = turns[index + 1] if index + 1 < len(turns) else None
        label = _real_status_label(turn, counter_actor="seller", next_turn=next_turn)
        yield _snapshot(
            task="seller_next_action",
            label=label,
            outcome_state=_outcome_state(label, turns=[turn]),
            listing=listing,
            turn=turn,
            history=turns[: index + 1],
            row_id=f"{_canonical_identifier(turn.get('thread_id'))}:{turn.get('turn_index')}:seller_next_action",
        )


def _buyer_response_to_counter_cases(listing: dict[str, Any], turns: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for index, turn in enumerate(turns):
        if turn.get("actor") != "seller" or turn.get("action") != "counter":
            continue
        next_turn = turns[index + 1] if index + 1 < len(turns) else None
        label = _real_status_label(turn, counter_actor="buyer", next_turn=next_turn)
        yield _snapshot(
            task="buyer_response_to_counter",
            label=label,
            outcome_state=_outcome_state(label, turns=[turn]),
            listing=listing,
            turn=turn,
            history=turns[: index + 1],
            row_id=f"{_canonical_identifier(turn.get('thread_id'))}:{turn.get('turn_index')}:buyer_response",
        )


def _agreement_case(listing: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, Any]:
    first = turns[0]
    label = agreement_label(turns)
    return _snapshot(
        task="agreement",
        label=label,
        outcome_state=_outcome_state(label, turns=turns),
        listing=listing,
        turn=first,
        history=[first],
        row_id=f"{_canonical_identifier(first.get('thread_id'))}:agreement",
    )


def _final_price_ratio_case(listing: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, Any]:
    first = turns[0]
    label = None
    if listing.get("sold_by_best_offer") is True and agreement_label(turns) == "1":
        final_sale_price = listing.get("final_sale_price")
        listing_price = listing.get("listing_price")
        if final_sale_price not in {None, ""} and listing_price not in {None, "", 0}:
            label = round(float(final_sale_price) / float(listing_price), 6)
    return _snapshot(
        task="final_price_ratio",
        label=label,
        outcome_state=_outcome_state(label, turns=turns),
        listing=listing,
        turn=first,
        history=[first],
        row_id=f"{_canonical_identifier(first.get('thread_id'))}:final_price_ratio",
    )


def _response_latency_cases(listing: dict[str, Any], turns: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for index, turn in enumerate(turns):
        label = None
        event_time = turn.get("event_time")
        response_time = turn.get("response_time")
        if event_time and response_time and _status_id(turn.get("status_id")) != 8:
            latency = (_parse_time(str(response_time)) - _parse_time(str(event_time))).total_seconds()
            if latency >= 0:
                label = latency
        yield _snapshot(
            task="response_latency",
            label=label,
            outcome_state=_outcome_state(label, turns=[turn]),
            listing=listing,
            turn=turn,
            history=turns[: index + 1],
            row_id=f"{_canonical_identifier(turn.get('thread_id'))}:{turn.get('turn_index')}:response_latency",
        )


def _case_record(case: dict[str, Any], counts: dict[str, dict[str, int | bool]]) -> tuple[Any, ...]:
    target = str(case["task"])
    counts[target]["eligible_rows"] = int(counts[target]["eligible_rows"]) + 1
    state = str(case["outcome_state"])
    if state == "supervised":
        counts[target]["supervised_rows"] = int(counts[target]["supervised_rows"]) + 1
    elif state == "censored":
        counts[target]["censored_outcome_rows"] = int(counts[target]["censored_outcome_rows"]) + 1
    else:
        counts[target]["unknown_outcome_rows"] = int(counts[target]["unknown_outcome_rows"]) + 1
    label = case.pop("label", None)
    if not validate_feature_contract([case]):
        raise BenchmarkV2Error(f"case contains forbidden or undeclared features: {case.get('row_id')}")
    token = _case_token(case)
    public_json = json.dumps(case, sort_keys=True)
    label_json = json.dumps({"label": label}, sort_keys=True) if state == "supervised" else None
    return (
        case["row_id"],
        target,
        case["timestamp"],
        _canonical_identifier(case["listing_id"]),
        _canonical_identifier(case["thread_id"]),
        _canonical_identifier(case.get("seller_id")),
        _canonical_identifier(case.get("buyer_id")),
        str(case.get("category") or ""),
        token,
        state,
        public_json,
        label_json,
    )


def _insert_case_batch(conn: sqlite3.Connection, batch: list[tuple[Any, ...]]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO cases (
            row_id, target, timestamp, listing_id, thread_id, seller_id,
            buyer_id, category, case_token, outcome_state, public_json, label_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        batch,
    )
    conn.commit()


def _write_public_task_files(conn: sqlite3.Connection, output_dir: Path, *, partition_rows: int) -> None:
    tasks_root = output_dir / "tasks"
    labels_root = output_dir / "protected_labels"
    tasks_root.mkdir(parents=True, exist_ok=True)
    labels_root.mkdir(parents=True, exist_ok=True)
    for target in TARGETS:
        target_dir = tasks_root / target
        target_dir.mkdir(parents=True, exist_ok=True)
        label_path = labels_root / f"{target}.jsonl"
        label_handle = label_path.open("w", encoding="utf-8", newline="\n")
        part_index = 0
        rows_in_part = 0
        handle = (target_dir / f"part-{part_index:05d}.jsonl").open("w", encoding="utf-8", newline="\n")
        try:
            for row_id, public_json, label_json in conn.execute(
                "SELECT row_id, public_json, label_json FROM cases WHERE target = ? ORDER BY timestamp, row_id",
                (target,),
            ):
                if rows_in_part >= partition_rows:
                    handle.close()
                    part_index += 1
                    rows_in_part = 0
                    handle = (target_dir / f"part-{part_index:05d}.jsonl").open("w", encoding="utf-8", newline="\n")
                handle.write(public_json + "\n")
                rows_in_part += 1
                if label_json is not None:
                    label_handle.write(json.dumps({"row_id": row_id, **json.loads(label_json)}, sort_keys=True) + "\n")
        finally:
            handle.close()
            label_handle.close()
        _write_file_manifest(target_dir)
        _write_file_manifest(labels_root, prefix=f"{target}.jsonl")


def _write_split_manifests(conn: sqlite3.Connection, output_dir: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    reports: dict[str, Any] = {}
    specs = {split["name"]: split for split in protocol.get("splits", [])}
    for split_name in SPLITS:
        split_root = output_dir / "splits" / split_name
        split_root.mkdir(parents=True, exist_ok=True)
        reports[split_name] = {**specs.get(split_name, {"name": split_name}), "targets": {}}
        for target in TARGETS:
            if split_name == "chronological_listing_purged":
                assignment, audit = _chronological_purged_assignment(conn, target, group_key="listing_id")
            elif split_name == "seller_disjoint":
                assignment, audit = _group_disjoint_assignment(conn, target, group_key="seller_id", missing_policy="error")
            elif split_name == "buyer_disjoint":
                assignment, audit = _group_disjoint_assignment(conn, target, group_key="buyer_id", missing_policy="exclude")
            elif split_name == "category_disjoint_diagnostic":
                assignment, audit = _group_disjoint_assignment(conn, target, group_key="category", missing_policy="error")
            elif split_name == "thread_safe_nested_development":
                assignment, audit = _group_disjoint_assignment(conn, target, group_key="thread_id", missing_policy="error")
            else:
                raise AssertionError(split_name)
            payload = _split_payload(split_name, target, assignment, audit, protocol_spec=specs.get(split_name, {}))
            path = split_root / f"{target}.json"
            _write_atomic_json(path, payload)
            (split_root / f"{target}.json.sha256").write_text(f"{sha256_file(path)}  {target}.json\n", encoding="utf-8")
            reports[split_name]["targets"][target] = {
                "manifest_path": str(path.as_posix()),
                "manifest_hash": payload["manifest_hash"],
                "row_counts": payload["row_counts"],
                "purged_rows": audit.get("purged_rows", 0),
                "missing_identifier_rows": audit.get("missing_identifier_rows", 0),
                "passed": payload["validation"]["passed"],
            }
    return reports


def _chronological_purged_assignment(conn: sqlite3.Connection, target: str, *, group_key: str) -> tuple[dict[str, list[str]], dict[str, Any]]:
    rows = list(
        conn.execute(
            f"SELECT row_id, {group_key} FROM cases WHERE target = ? ORDER BY timestamp, row_id",
            (target,),
        )
    )
    boundaries = _split_boundaries(len(rows))
    provisional: list[tuple[str, str, str]] = []
    for index, (row_id, group) in enumerate(rows):
        region = _region_for_index(index, boundaries)
        provisional.append((region, row_id, _canonical_identifier(group)))
    group_regions: dict[str, set[str]] = {}
    for region, _row_id, group in provisional:
        group_regions.setdefault(group, set()).add(region)
    purged_groups = {group for group, regions in group_regions.items() if len(regions) > 1}
    assignment = {"train": [], "development": [], "hidden": []}
    for region, row_id, group in provisional:
        if group in purged_groups:
            continue
        assignment[region].append(row_id)
    retained_group_regions: dict[str, set[str]] = {}
    for region, row_id, group in provisional:
        if group not in purged_groups:
            retained_group_regions.setdefault(group, set()).add(region)
    return assignment, {
        "group_key": group_key,
        "purged_group_ids": sorted(purged_groups),
        "purged_rows": sum(1 for _region, _row_id, group in provisional if group in purged_groups),
        "missing_identifier_rows": 0,
        "group_disjoint": all(len(regions) == 1 for regions in retained_group_regions.values()),
    }


def _group_disjoint_assignment(
    conn: sqlite3.Connection,
    target: str,
    *,
    group_key: str,
    missing_policy: str,
) -> tuple[dict[str, list[str]], dict[str, Any]]:
    rows = [
        (str(row_id), _canonical_identifier(group))
        for row_id, group in conn.execute(
            f"SELECT row_id, {group_key} FROM cases WHERE target = ? ORDER BY row_id",
            (target,),
        )
    ]
    missing_rows = [row_id for row_id, group in rows if not group]
    if missing_rows and missing_policy == "error":
        raise BenchmarkV2Error(f"missing {group_key} for required split {target}")
    usable = [(row_id, group) for row_id, group in rows if group]
    groups = sorted({group for _row_id, group in usable}, key=lambda value: stable_hash({"group": value, "split": group_key}))
    boundaries = _split_boundaries(len(groups))
    group_region: dict[str, str] = {
        group: _region_for_index(index, boundaries)
        for index, group in enumerate(groups)
    }
    assignment = {"train": [], "development": [], "hidden": []}
    for row_id, group in usable:
        assignment[group_region[group]].append(row_id)
    for region in assignment:
        assignment[region].sort()
    retained_group_regions: dict[str, set[str]] = {}
    for row_id, group in usable:
        retained_group_regions.setdefault(group, set()).add(group_region[group])
    return assignment, {
        "group_key": group_key,
        "purged_group_ids": [],
        "purged_rows": len(missing_rows) if missing_policy == "exclude" else 0,
        "missing_identifier_rows": len(missing_rows),
        "group_disjoint": all(len(regions) == 1 for regions in retained_group_regions.values()),
    }


def _split_payload(
    split_name: str,
    target: str,
    assignment: dict[str, list[str]],
    audit: dict[str, Any],
    *,
    protocol_spec: dict[str, Any],
) -> dict[str, Any]:
    row_counts = {region: len(rows) for region, rows in assignment.items()}
    validation = _validate_assignment(assignment, audit)
    payload = {
        "schema_version": "offerlab_benchmark_v2_split_manifest.v1",
        "benchmark_id": "offerlab_benchmark_v2",
        "split": split_name,
        "target": target,
        "protocol_spec": protocol_spec,
        "row_counts": row_counts,
        "row_counts_hash": stable_hash(row_counts),
        "case_set_hash": stable_hash(assignment),
        "assignments": assignment,
        "purged_group_ids": audit.get("purged_group_ids", []),
        "purged_rows": audit.get("purged_rows", 0),
        "missing_identifier_rows": audit.get("missing_identifier_rows", 0),
        "validation": validation,
        "immutable": True,
    }
    payload["manifest_hash"] = stable_hash(payload)
    return payload


def _validate_assignment(assignment: dict[str, list[str]], audit: dict[str, Any]) -> dict[str, Any]:
    all_ids = assignment["train"] + assignment["development"] + assignment["hidden"]
    no_duplicate_cases = len(all_ids) == len(set(all_ids))
    group_disjoint = audit.get("group_disjoint", True) is True
    return {
        "passed": no_duplicate_cases and group_disjoint,
        "no_duplicate_cases_across_regions": no_duplicate_cases,
        "group_disjoint": group_disjoint,
        "group_key": audit.get("group_key", ""),
    }


def _write_fresh_hidden_lockbox(
    conn: sqlite3.Connection,
    output_dir: Path,
    *,
    protocol: dict[str, Any],
    v1_final_manifest: dict[str, Any],
    external_v1_hidden_tokens: list[str],
    partition_rows: int,
) -> dict[str, Any]:
    split_root = output_dir / "fresh_hidden_lockbox"
    split_root.mkdir(parents=True, exist_ok=True)
    reports: dict[str, Any] = {}
    for target in TARGETS:
        chronological = _read_json(output_dir / "splits" / "chronological_listing_purged" / f"{target}.json")
        excluded_tokens = _v1_manifest_hidden_tokens(v1_final_manifest) | set(external_v1_hidden_tokens)
        kept_ids: list[str] = []
        kept_tokens: list[str] = []
        excluded_overlap_rows = 0
        hidden_ids = chronological["assignments"]["hidden"]
        for row_id, token in conn.execute(
            f"SELECT row_id, case_token FROM cases WHERE row_id IN ({_placeholders(hidden_ids)}) ORDER BY row_id",
            hidden_ids,
        ) if hidden_ids else []:
            if token in excluded_tokens:
                excluded_overlap_rows += 1
                continue
            kept_ids.append(row_id)
            kept_tokens.append(token)
        validation = validate_v2_hidden_exclusion(
            v2_manifest=protocol,
            v1_final_manifest=v1_final_manifest,
            candidate_hidden_case_tokens=kept_tokens or [f"empty-candidate:{target}"],
            external_v1_hidden_case_tokens=external_v1_hidden_tokens,
        )
        payload = {
            "schema_version": "offerlab_benchmark_v2_fresh_hidden_lockbox_manifest.v1",
            "benchmark_id": "offerlab_benchmark_v2",
            "target": target,
            "query_budget": 1,
            "assignments": {"hidden": kept_ids},
            "row_counts": {"hidden": len(kept_ids)},
            "case_set_hash": stable_hash(kept_ids),
            "hidden_case_tokens_hash": stable_hash(
                [
                    token
                    for (token,) in conn.execute(
                        f"SELECT case_token FROM cases WHERE row_id IN ({_placeholders(kept_ids)}) ORDER BY row_id",
                        kept_ids,
                    )
                ] if kept_ids else []
            ),
            "v1_exclusion_cases": validation.v1_exclusion_cases,
            "candidate_hidden_cases": validation.candidate_hidden_cases,
            "excluded_overlap_rows": excluded_overlap_rows,
            "labels_redacted_from_public_tasks": True,
            "immutable": True,
        }
        payload["manifest_hash"] = stable_hash(payload)
        path = split_root / f"{target}.json"
        _write_atomic_json(path, payload)
        (split_root / f"{target}.json.sha256").write_text(f"{sha256_file(path)}  {target}.json\n", encoding="utf-8")
        _write_hidden_public_partitions(conn, split_root / target, kept_ids, partition_rows=partition_rows)
        reports[target] = {
            "manifest_path": str(path.as_posix()),
            "manifest_hash": payload["manifest_hash"],
            "hidden_rows": len(kept_ids),
            "excluded_overlap_rows": excluded_overlap_rows,
            "v1_exclusion_cases": validation.v1_exclusion_cases,
            "labels_in_public_lockbox": False,
        }
    return reports


def _write_hidden_public_partitions(conn: sqlite3.Connection, target_dir: Path, row_ids: list[str], *, partition_rows: int) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    if not row_ids:
        (target_dir / "part-00000.jsonl").write_text("", encoding="utf-8")
        return
    part_index = 0
    rows_in_part = 0
    handle = (target_dir / f"part-{part_index:05d}.jsonl").open("w", encoding="utf-8", newline="\n")
    try:
        for (public_json,) in conn.execute(
            f"SELECT public_json FROM cases WHERE row_id IN ({_placeholders(row_ids)}) ORDER BY timestamp, row_id",
            row_ids,
        ):
            if rows_in_part >= partition_rows:
                handle.close()
                part_index += 1
                rows_in_part = 0
                handle = (target_dir / f"part-{part_index:05d}.jsonl").open("w", encoding="utf-8", newline="\n")
            row = json.loads(public_json)
            row.pop("label", None)
            row["label_redacted"] = True
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            rows_in_part += 1
    finally:
        handle.close()


def _snapshot(
    *,
    task: str,
    label: Any,
    outcome_state: str,
    listing: dict[str, Any],
    turn: dict[str, Any],
    history: list[dict[str, Any]],
    row_id: str,
) -> dict[str, Any]:
    features = {
        "category": listing.get("category"),
        "condition": listing.get("condition"),
        "listing_price": listing.get("listing_price"),
        "current_actor": turn.get("actor"),
        "current_action": turn.get("action"),
        "current_amount": turn.get("amount"),
        "offer_to_asking_ratio": (float(turn["amount"]) / float(listing["listing_price"])) if turn.get("amount") and listing.get("listing_price") else None,
        "round_number": turn.get("turn_index"),
        "prior_turn_count": len(history) - 1,
        "prior_counter_count": sum(1 for item in history[:-1] if item.get("action") == "counter"),
    }
    return {
        "row_id": row_id,
        "task": task,
        "label": label,
        "outcome_state": outcome_state,
        "features": features,
        "observed_history": [_sanitize_history_turn(item) for item in history],
        "thread_id": _canonical_identifier(turn.get("thread_id")),
        "listing_id": _canonical_identifier(listing.get("listing_id")),
        "seller_id": _canonical_identifier(listing.get("seller_id") or turn.get("seller_id")),
        "buyer_id": _canonical_identifier(turn.get("buyer_id")),
        "category": str(listing.get("category") or ""),
        "timestamp": str(turn.get("event_time") or ""),
    }


def _real_status_label(turn: dict[str, Any], *, counter_actor: str, next_turn: dict[str, Any] | None) -> str | None:
    status_id = _status_id(turn.get("status_id"))
    if status_id in {1, 9}:
        return "accept"
    if status_id in {2, 6}:
        return "decline"
    if status_id == 0:
        return "expire"
    if status_id == 7:
        if next_turn is not None and next_turn.get("actor") == counter_actor and next_turn.get("action") == "counter":
            return "counter"
        return None
    if status_id == 8:
        return None
    return None


def _outcome_state(label: Any, *, turns: list[dict[str, Any]]) -> str:
    if label is not None:
        return "supervised"
    if any(_status_id(turn.get("status_id")) == 8 for turn in turns):
        return "censored"
    return "unknown"


def _sanitize_history_turn(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        "turn_index": turn.get("turn_index"),
        "actor": turn.get("actor"),
        "action": turn.get("action"),
        "amount": turn.get("amount"),
    }


def _iter_partitioned_table(normalized_dir: Path, manifest: dict[str, Any], table_name: str) -> Iterator[dict[str, Any]]:
    table = manifest["tables"][table_name]
    for partition in table.get("partitions", []):
        path = Path(partition["path"])
        if not path.is_absolute():
            path = normalized_dir / path
        if path.suffix == ".parquet":
            import pyarrow.parquet as pq

            table_data = pq.read_table(path)
            for row in table_data.to_pylist():
                yield row
        else:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        yield json.loads(line)


def _write_file_manifest(directory: Path, *, prefix: str | None = None) -> None:
    files = []
    paths = [directory / prefix] if prefix else sorted(directory.glob("part-*.jsonl"))
    for path in paths:
        if path.exists():
            files.append({"path": str(path.as_posix()), "rows": _line_count(path), "sha256": sha256_file(path)})
    manifest = {"files": files, "rows": sum(item["rows"] for item in files), "hash": stable_hash(files)}
    _write_atomic_json(directory / ((prefix or "manifest") + ".manifest.json" if prefix else "manifest.json"), manifest)


def _load_external_v1_hidden_tokens(path: Path | None) -> list[str]:
    if path is None:
        return []
    payload = _read_json(path)
    if isinstance(payload, list):
        tokens = payload
    else:
        tokens = payload.get("tokens", [])
    return sorted({str(token).strip() for token in tokens if str(token).strip()})


def _v1_manifest_hidden_tokens(v1_final_manifest: dict[str, Any]) -> set[str]:
    token_block = v1_final_manifest.get("hidden_lockbox", {}).get("case_tokens", {})
    tokens = token_block.get("tokens", []) if isinstance(token_block, dict) else []
    return {str(token).strip() for token in tokens if str(token).strip()}


def _split_boundaries(count: int) -> tuple[int, int]:
    if count <= 0:
        return (0, 0)
    train_end = max(1, int(count * 0.6))
    development_end = max(train_end, int(count * 0.8))
    if count >= 3:
        development_end = min(development_end, count - 1)
    return train_end, development_end


def _region_for_index(index: int, boundaries: tuple[int, int]) -> str:
    train_end, development_end = boundaries
    if index < train_end:
        return "train"
    if index < development_end:
        return "development"
    return "hidden"


def _case_token(row: dict[str, Any]) -> str:
    return stable_hash(
        {
            "task": row.get("task"),
            "row_id": row.get("row_id"),
            "thread_id": row.get("thread_id"),
            "listing_id": row.get("listing_id"),
            "timestamp": row.get("timestamp"),
            "features": row.get("features", {}),
            "observed_history": row.get("observed_history", []),
        }
    )


def _canonical_identifier(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _status_id(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(str(value)))
        except (TypeError, ValueError):
            return None


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _placeholders(values: list[str]) -> str:
    if not values:
        return "NULL"
    return ",".join("?" for _ in values)


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, newline="\n") as handle:
        temp = Path(handle.name)
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp, path)


def _line_count(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[3], text=True).strip()
    except Exception:
        return None
