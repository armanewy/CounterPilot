from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import tracemalloc
from typing import Any, Iterable

from behavior_lab.datasets.nber_best_offer.real_normalize import (
    OFFICIAL_FULL_SOURCE_EXPECTATIONS,
    _find_source,
    _float_or_none,
    _int_or_none,
    _open_text,
)
from behavior_lab.datasets.nber_best_offer.source_schema import REAL_LISTING_COLUMNS, sha256_file, validate_real_headers


FULL_LISTING_PASS_VERSION = "nber_full_listing_pass.v1"
FINAL_CONTRACT_VERSION = "2020_qje_final_released_code_v1"


class FullListingPassError(ValueError):
    pass


def default_output_dir() -> Path:
    return Path(os.environ.get("OFFERLAB_DATA_ROOT", r"C:\OfferLabData")) / "processed" / "nber_best_offer_full" / "listing_restrictions"


def build_full_listing_restrictions(
    raw_dir: str | Path,
    output_dir: str | Path | None = None,
    *,
    partitions: int = 128,
    resume: bool = True,
    require_official_sources: bool = False,
) -> dict[str, Any]:
    if partitions <= 0:
        raise FullListingPassError("partitions must be positive")
    start = time.perf_counter()
    raw = Path(raw_dir)
    output = Path(output_dir) if output_dir is not None else default_output_dir()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    lists_path = _find_source(raw, "anon_bo_lists.csv")
    source_hash = sha256_file(lists_path)
    source_bytes = lists_path.stat().st_size
    header = _read_header(lists_path)
    header_validation = validate_real_headers(listings=header)
    if not header_validation["valid"]:
        raise FullListingPassError(json.dumps(header_validation, sort_keys=True))
    official = _official_listing_source(source_hash, source_bytes)
    if require_official_sources and not official["matches_expected_official_source"]:
        raise FullListingPassError(json.dumps(official, sort_keys=True))
    signature = {
        "schema_version": "nber_full_listing_pass_signature.v1",
        "pass_version": FULL_LISTING_PASS_VERSION,
        "contract_version": FINAL_CONTRACT_VERSION,
        "raw_dir": str(raw.resolve()),
        "source_path": str(lists_path.resolve()),
        "source_sha256": source_hash,
        "source_bytes": source_bytes,
        "partitions": partitions,
        "header_validation": header_validation,
    }
    if resume and manifest_path.exists():
        current = _load_json(manifest_path)
        if current and current.get("signature") == signature and inspect_full_listing_restrictions(output)["valid"]:
            current["idempotent_rerun"] = True
            return current

    work_dir = output / "_work"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    table_dir = work_dir / "listing_restrictions"
    table_dir.mkdir(parents=True)
    quarantine_dir = work_dir / "quarantine"
    quarantine_dir.mkdir(parents=True)
    bucket_dir = work_dir / "source_listing_buckets"
    bucket_dir.mkdir(parents=True)

    tracemalloc.start()
    duplicate_quarantine_path = quarantine_dir / "duplicate_listing_ids.jsonl"
    bucket_manifest = _bucket_listing_rows(lists_path, bucket_dir, partitions=partitions)
    stats, seller_stats, partition_rows = _process_listing_buckets(
        bucket_dir,
        table_dir,
        duplicate_quarantine_path,
        source_hash=source_hash,
        partitions=partitions,
        raw_source_listing_count=bucket_manifest["source_rows"],
        missing_listing_id_count=bucket_manifest["missing_listing_id_count"],
    )
    duplicate_hash = _sha256_if_nonempty(duplicate_quarantine_path)
    current_memory, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    partitions_payload = _finalize_partitions(table_dir, partition_rows)
    quarantine_payload = {
        "duplicate_listing_ids": {
            "path": str(duplicate_quarantine_path.resolve()),
            "rows": int(stats["duplicate_listing_id_count"]),
            "sha256": duplicate_hash,
        }
    }
    manifest = {
        "schema_version": FULL_LISTING_PASS_VERSION,
        "status": "complete",
        "signature": signature,
        "source_files": {
            "anon_bo_lists": {
                "path": str(lists_path.resolve()),
                "sha256": source_hash,
                "bytes": source_bytes,
            }
        },
        "official_source_contract": official,
        "header_validation": header_validation,
        "bucket_manifest": bucket_manifest,
        "table": {
            "name": "listing_restrictions",
            "format": "jsonl",
            "path": str((output / "listing_restrictions").resolve()),
            "rows": int(stats["accepted_unique_listing_count"]),
            "partitions": partitions_payload,
            "key": "listing_id",
            "restriction_contract_version": FINAL_CONTRACT_VERSION,
            "columns": [
                "listing_id",
                "seller_hash",
                "start_price",
                "item_price",
                "condition_id",
                "used",
                "seller_feedback_positive_percent",
                "L1_violation",
                "L2_violation",
                "T1_violation",
                "T2_buyer_violation",
                "T2_seller_violation",
                "T3_violation",
                "T4_violation",
                "T5_violation",
                "eligible_l1_l2",
                "source_hash",
                "source_row_hash",
                "restriction_contract_version",
            ],
        },
        "summary": {
            **_finalize_stats(stats),
            **seller_stats,
        },
        "quarantine": quarantine_payload,
        "runtime_seconds": round(time.perf_counter() - start, 3),
        "peak_tracemalloc_bytes": int(peak_memory),
        "current_tracemalloc_bytes_at_stop": int(current_memory),
        "resume_strategy": "A complete manifest with matching signature and verified partitions is reused idempotently. Partial work is written under _work and promoted atomically only after all partitions are hashed.",
    }
    _promote_work(output, work_dir, manifest, manifest_path)
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def inspect_full_listing_restrictions(output_dir: str | Path | None = None) -> dict[str, Any]:
    output = Path(output_dir) if output_dir is not None else default_output_dir()
    manifest_path = output / "manifest.json"
    if not manifest_path.exists():
        return {"schema_version": "nber_full_listing_pass_inspection.v1", "valid": False, "failures": ["missing_manifest"], "output_dir": str(output)}
    manifest = _load_json(manifest_path)
    if not manifest:
        return {"schema_version": "nber_full_listing_pass_inspection.v1", "valid": False, "failures": ["invalid_manifest"], "output_dir": str(output)}
    failures = []
    table = manifest.get("table", {})
    partition_rows = 0
    seen_paths: set[str] = set()
    for partition in table.get("partitions", []):
        path_text = partition.get("path")
        if not path_text:
            failures.append("missing_partition_path")
            continue
        if path_text in seen_paths:
            failures.append(f"duplicate_partition_path:{path_text}")
        seen_paths.add(path_text)
        path = Path(path_text)
        if not path.exists():
            failures.append(f"missing_partition:{path.name}")
            continue
        if sha256_file(path) != partition.get("sha256"):
            failures.append(f"partition_hash_mismatch:{path.name}")
        rows = _line_count(path)
        if rows != int(partition.get("rows", -1)):
            failures.append(f"partition_row_count_mismatch:{path.name}")
        partition_rows += rows
    if partition_rows != int(table.get("rows", -1)):
        failures.append(f"table_row_sum_mismatch:{partition_rows}!={table.get('rows')}")
    source = manifest.get("source_files", {}).get("anon_bo_lists", {})
    source_path = Path(source.get("path", ""))
    if not source_path.exists():
        failures.append("missing_source_file")
    elif sha256_file(source_path) != source.get("sha256"):
        failures.append("source_hash_mismatch")
    quarantine = manifest.get("quarantine", {}).get("duplicate_listing_ids", {})
    qpath = Path(quarantine.get("path", ""))
    if quarantine.get("rows", 0) and (not qpath.exists() or sha256_file(qpath) != quarantine.get("sha256")):
        failures.append("duplicate_quarantine_hash_mismatch")
    return {
        "schema_version": "nber_full_listing_pass_inspection.v1",
        "valid": not failures,
        "failures": failures,
        "output_dir": str(output.resolve()),
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": sha256_file(manifest_path),
        "rows": table.get("rows"),
        "partitions": len(table.get("partitions", [])),
        "summary": manifest.get("summary", {}),
    }


def _bucket_listing_rows(lists_path: Path, bucket_dir: Path, *, partitions: int) -> dict[str, Any]:
    handles = [(bucket_dir / f"bucket-{index:04d}.tsv").open("w", encoding="utf-8", newline="\n") for index in range(partitions)]
    writers = [csv.writer(handle, delimiter="\t", lineterminator="\n") for handle in handles]
    bucket_rows = [0 for _ in range(partitions)]
    source_rows = 0
    missing_listing_id_count = 0
    progress_path = bucket_dir.parent / "bucket_progress.json"
    try:
        with _open_text(lists_path) as handle:
            reader = csv.DictReader(handle)
            for line_number, row in enumerate(reader, start=2):
                source_rows += 1
                listing_id = str(row.get("anon_item_id", "")).strip()
                if not listing_id:
                    missing_listing_id_count += 1
                    continue
                bucket = _bucket_for(listing_id, partitions)
                writers[bucket].writerow(
                    [
                        listing_id,
                        line_number,
                        _listing_row_hash(row),
                        str(row.get("anon_slr_id", "")),
                        str(row.get("start_price_usd", "")),
                        str(row.get("item_price", "")),
                        str(row.get("item_cndtn_id", "")),
                        str(row.get("fdbk_pstv_start", "")),
                    ]
                )
                bucket_rows[bucket] += 1
                if source_rows % 1_000_000 == 0:
                    for bucket_handle in handles:
                        bucket_handle.flush()
                    _write_atomic_json(
                        progress_path,
                        {
                            "schema_version": "nber_full_listing_bucket_progress.v1",
                            "source_rows_seen": source_rows,
                            "bucketed_rows": sum(bucket_rows),
                            "missing_listing_id_count": missing_listing_id_count,
                        },
                    )
    finally:
        for handle in handles:
            handle.close()
    _write_atomic_json(
        progress_path,
        {
            "schema_version": "nber_full_listing_bucket_progress.v1",
            "source_rows_seen": source_rows,
            "bucketed_rows": sum(bucket_rows),
            "missing_listing_id_count": missing_listing_id_count,
            "complete": True,
        },
    )
    return {
        "schema_version": "nber_full_listing_source_buckets.v1",
        "source_rows": source_rows,
        "missing_listing_id_count": missing_listing_id_count,
        "partitions": [
            {
                "partition_index": index,
                "rows": bucket_rows[index],
            }
            for index in range(partitions)
        ],
        "dedupe_strategy": "Rows are bucketed by listing_id hash; each bucket is deduplicated in memory, preserving the first source row and quarantining later duplicate listing IDs.",
    }


def _process_listing_buckets(
    bucket_dir: Path,
    table_dir: Path,
    duplicate_quarantine_path: Path,
    *,
    source_hash: str,
    partitions: int,
    raw_source_listing_count: int,
    missing_listing_id_count: int,
) -> tuple[dict[str, Any], dict[str, int], list[int]]:
    stats = _new_stats()
    stats["raw_source_listing_count"] = raw_source_listing_count
    stats["missing_listing_id_count"] = missing_listing_id_count
    partition_rows = [0 for _ in range(partitions)]
    sellers_before: dict[str, float | None] = {}
    sellers_after_l1_l2: dict[str, float | None] = {}
    with duplicate_quarantine_path.open("w", encoding="utf-8", newline="\n") as duplicate_handle:
        for index in range(partitions):
            seen: set[str] = set()
            input_path = bucket_dir / f"bucket-{index:04d}.tsv"
            output_path = table_dir / f"part-{index:04d}.jsonl.tmp"
            with input_path.open("r", encoding="utf-8", newline="") as source, output_path.open("w", encoding="utf-8", newline="\n") as output:
                reader = csv.reader(source, delimiter="\t")
                for listing_id, line_number, source_row_hash, seller_id, start_price_text, item_price_text, condition_text, feedback_text in reader:
                    if listing_id in seen:
                        stats["duplicate_listing_id_count"] += 1
                        duplicate_handle.write(
                            json.dumps(
                                {
                                    "listing_id": listing_id,
                                    "line_number": int(line_number),
                                    "source_row_hash": source_row_hash,
                                    "reason": "duplicate_listing_id",
                                },
                                sort_keys=True,
                            )
                            + "\n"
                        )
                        continue
                    seen.add(listing_id)
                    row = _listing_restriction_row(
                        listing_id=listing_id,
                        seller_id=seller_id,
                        start_price_text=start_price_text,
                        item_price_text=item_price_text,
                        condition_text=condition_text,
                        feedback_text=feedback_text,
                        source_hash=source_hash,
                        source_row_hash=source_row_hash,
                    )
                    output.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                    partition_rows[index] += 1
                    _update_stats(stats, row["start_price"], row["condition_id"], row["used"], row["L1_violation"], row["L2_violation"])
                    stats["accepted_unique_listing_count"] += 1
                    seller_hash = row["seller_hash"]
                    if seller_hash is not None:
                        _update_seller_feedback(sellers_before, seller_hash, row["seller_feedback_positive_percent"])
                        if row["eligible_l1_l2"]:
                            _update_seller_feedback(sellers_after_l1_l2, seller_hash, row["seller_feedback_positive_percent"])
    return stats, _seller_stats_from_maps(sellers_before, sellers_after_l1_l2), partition_rows


def _listing_restriction_row(
    *,
    listing_id: str,
    seller_id: str,
    start_price_text: str,
    item_price_text: str,
    condition_text: str,
    feedback_text: str,
    source_hash: str,
    source_row_hash: str,
) -> dict[str, Any]:
    start_price = _float_or_none(start_price_text)
    item_price = _float_or_none(item_price_text)
    condition_id = _int_or_none(condition_text)
    seller_feedback = _float_or_none(feedback_text)
    seller_hash = _hash_identifier(seller_id)
    l1 = bool(start_price is not None and start_price > 1000)
    l2 = bool(item_price is not None and start_price is not None and item_price > start_price)
    used = None if condition_id is None else condition_id >= 3000
    return {
        "listing_id": listing_id,
        "seller_hash": seller_hash,
        "start_price": start_price,
        "item_price": item_price,
        "condition_id": condition_id,
        "used": used,
        "seller_feedback_positive_percent": seller_feedback,
        "L1_violation": l1,
        "L2_violation": l2,
        "T1_violation": None,
        "T2_buyer_violation": None,
        "T2_seller_violation": None,
        "T3_violation": None,
        "T4_violation": None,
        "T5_violation": None,
        "eligible_l1_l2": not l1 and not l2,
        "source_hash": source_hash,
        "source_row_hash": source_row_hash,
        "restriction_contract_version": FINAL_CONTRACT_VERSION,
    }


def _update_seller_feedback(store: dict[str, float | None], seller_hash: str, feedback: float | None) -> None:
    current = store.get(seller_hash)
    if seller_hash not in store:
        store[seller_hash] = feedback
    elif feedback is not None and current is not None:
        store[seller_hash] = max(current, feedback)
    elif feedback is not None:
        store[seller_hash] = feedback


def _seller_stats_from_maps(before: dict[str, float | None], after_l1_l2: dict[str, float | None]) -> dict[str, int]:
    return {
        "seller_count_before_l1_l2": len(before),
        "seller_count_after_l1_l2": len(after_l1_l2),
        "seller_feedback_nonmissing_denominator_before_l1_l2": sum(1 for value in before.values() if value is not None),
        "seller_feedback_nonmissing_denominator_after_l1_l2": sum(1 for value in after_l1_l2.values() if value is not None),
    }


def _consume_listing_row(
    conn: sqlite3.Connection,
    row: dict[str, str],
    *,
    line_number: int,
    source_hash: str,
    partition_handles: list[Any],
    partition_rows: list[int],
    duplicate_handle: Any,
    stats: dict[str, Any],
    partitions: int,
) -> None:
    stats["raw_source_listing_count"] += 1
    listing_id = str(row.get("anon_item_id", "")).strip()
    if not listing_id:
        stats["missing_listing_id_count"] += 1
        return
    cursor = conn.execute("INSERT OR IGNORE INTO listing_ids (listing_id) VALUES (?)", (listing_id,))
    if cursor.rowcount == 0:
        stats["duplicate_listing_id_count"] += 1
        duplicate_handle.write(
            json.dumps(
                {
                    "listing_id": listing_id,
                    "line_number": line_number,
                    "source_row_hash": _row_hash(row),
                    "reason": "duplicate_listing_id",
                },
                sort_keys=True,
            )
            + "\n"
        )
        return
    start_price = _float_or_none(row.get("start_price_usd"))
    item_price = _float_or_none(row.get("item_price"))
    condition_id = _int_or_none(row.get("item_cndtn_id"))
    seller_feedback = _float_or_none(row.get("fdbk_pstv_start"))
    seller_hash = _hash_identifier(row.get("anon_slr_id"))
    l1 = bool(start_price is not None and start_price > 1000)
    l2 = bool(item_price is not None and start_price is not None and item_price > start_price)
    eligible_l1_l2 = not l1 and not l2
    used = None if condition_id is None else condition_id >= 3000
    source_row_hash = _row_hash(row)
    out = {
        "listing_id": listing_id,
        "seller_hash": seller_hash,
        "start_price": start_price,
        "item_price": item_price,
        "condition_id": condition_id,
        "used": used,
        "seller_feedback_positive_percent": seller_feedback,
        "L1_violation": l1,
        "L2_violation": l2,
        "T1_violation": None,
        "T2_buyer_violation": None,
        "T2_seller_violation": None,
        "T3_violation": None,
        "T4_violation": None,
        "T5_violation": None,
        "eligible_l1_l2": eligible_l1_l2,
        "source_hash": source_hash,
        "source_row_hash": source_row_hash,
        "restriction_contract_version": FINAL_CONTRACT_VERSION,
    }
    bucket = _bucket_for(listing_id, partitions)
    partition_handles[bucket].write(json.dumps(out, sort_keys=True, separators=(",", ":")) + "\n")
    partition_rows[bucket] += 1
    _update_stats(stats, start_price, condition_id, used, l1, l2)
    stats["accepted_unique_listing_count"] += 1
    if seller_hash is not None:
        conn.execute(
            """
            INSERT INTO sellers_before VALUES (?, ?)
            ON CONFLICT(seller_hash) DO UPDATE SET
                feedback = CASE
                    WHEN excluded.feedback IS NULL THEN sellers_before.feedback
                    WHEN sellers_before.feedback IS NULL THEN excluded.feedback
                    ELSE MAX(sellers_before.feedback, excluded.feedback)
                END
            """,
            (seller_hash, seller_feedback),
        )
        if eligible_l1_l2:
            conn.execute(
                """
                INSERT INTO sellers_after_l1_l2 VALUES (?, ?)
                ON CONFLICT(seller_hash) DO UPDATE SET
                    feedback = CASE
                        WHEN excluded.feedback IS NULL THEN sellers_after_l1_l2.feedback
                        WHEN sellers_after_l1_l2.feedback IS NULL THEN excluded.feedback
                        ELSE MAX(sellers_after_l1_l2.feedback, excluded.feedback)
                    END
                """,
                (seller_hash, seller_feedback),
            )


def _configure_sqlite(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=FILE")
    conn.executescript(
        """
        CREATE TABLE listing_ids (listing_id TEXT PRIMARY KEY) WITHOUT ROWID;
        CREATE TABLE sellers_before (seller_hash TEXT PRIMARY KEY, feedback REAL) WITHOUT ROWID;
        CREATE TABLE sellers_after_l1_l2 (seller_hash TEXT PRIMARY KEY, feedback REAL) WITHOUT ROWID;
        """
    )


def _new_stats() -> dict[str, Any]:
    return {
        "raw_source_listing_count": 0,
        "accepted_unique_listing_count": 0,
        "duplicate_listing_id_count": 0,
        "missing_listing_id_count": 0,
        "L1_violation_count": 0,
        "L2_violation_count": 0,
        "eligible_l1_l2_count": 0,
        "listing_price_count": 0,
        "listing_price_missing_count": 0,
        "listing_price_sum": 0.0,
        "listing_price_min": None,
        "listing_price_max": None,
        "listing_price_histogram": {
            "missing": 0,
            "0_to_10": 0,
            "10_to_25": 0,
            "25_to_50": 0,
            "50_to_100": 0,
            "100_to_250": 0,
            "250_to_500": 0,
            "500_to_1000": 0,
            "over_1000": 0,
        },
        "used_true_numerator": 0,
        "used_false_count": 0,
        "used_nonmissing_denominator": 0,
        "used_missing_count": 0,
    }


def _update_stats(stats: dict[str, Any], start_price: float | None, condition_id: int | None, used: bool | None, l1: bool, l2: bool) -> None:
    if l1:
        stats["L1_violation_count"] += 1
    if l2:
        stats["L2_violation_count"] += 1
    if not l1 and not l2:
        stats["eligible_l1_l2_count"] += 1
    if start_price is None:
        stats["listing_price_missing_count"] += 1
        stats["listing_price_histogram"]["missing"] += 1
    else:
        stats["listing_price_count"] += 1
        stats["listing_price_sum"] += start_price
        stats["listing_price_min"] = start_price if stats["listing_price_min"] is None else min(stats["listing_price_min"], start_price)
        stats["listing_price_max"] = start_price if stats["listing_price_max"] is None else max(stats["listing_price_max"], start_price)
        stats["listing_price_histogram"][_price_bucket(start_price)] += 1
    if condition_id is None:
        stats["used_missing_count"] += 1
    else:
        stats["used_nonmissing_denominator"] += 1
        if used:
            stats["used_true_numerator"] += 1
        else:
            stats["used_false_count"] += 1


def _finalize_stats(stats: dict[str, Any]) -> dict[str, Any]:
    price_count = stats["listing_price_count"]
    used_denominator = stats["used_nonmissing_denominator"]
    return {
        "raw_source_listing_count": int(stats["raw_source_listing_count"]),
        "accepted_unique_listing_count": int(stats["accepted_unique_listing_count"]),
        "duplicate_listing_id_count": int(stats["duplicate_listing_id_count"]),
        "missing_listing_id_count": int(stats["missing_listing_id_count"]),
        "L1_violation_count": int(stats["L1_violation_count"]),
        "L2_violation_count": int(stats["L2_violation_count"]),
        "eligible_l1_l2_count": int(stats["eligible_l1_l2_count"]),
        "listing_price_distribution": {
            "count": int(price_count),
            "missing_count": int(stats["listing_price_missing_count"]),
            "min": stats["listing_price_min"],
            "max": stats["listing_price_max"],
            "mean": (stats["listing_price_sum"] / price_count) if price_count else None,
            "histogram": dict(stats["listing_price_histogram"]),
        },
        "used_true_numerator": int(stats["used_true_numerator"]),
        "used_false_count": int(stats["used_false_count"]),
        "used_nonmissing_denominator": int(used_denominator),
        "used_missing_count": int(stats["used_missing_count"]),
        "used_rate": (stats["used_true_numerator"] / used_denominator) if used_denominator else None,
        "thread_restriction_fields": "unset_until_joined_from_thread_restriction_pass",
    }


def _seller_stats(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "seller_count_before_l1_l2": int(conn.execute("SELECT COUNT(*) FROM sellers_before").fetchone()[0]),
        "seller_count_after_l1_l2": int(conn.execute("SELECT COUNT(*) FROM sellers_after_l1_l2").fetchone()[0]),
        "seller_feedback_nonmissing_denominator_before_l1_l2": int(conn.execute("SELECT COUNT(*) FROM sellers_before WHERE feedback IS NOT NULL").fetchone()[0]),
        "seller_feedback_nonmissing_denominator_after_l1_l2": int(conn.execute("SELECT COUNT(*) FROM sellers_after_l1_l2 WHERE feedback IS NOT NULL").fetchone()[0]),
    }


def _finalize_partitions(table_dir: Path, row_counts: list[int]) -> list[dict[str, Any]]:
    partitions = []
    for index, rows in enumerate(row_counts):
        tmp = table_dir / f"part-{index:04d}.jsonl.tmp"
        digest = sha256_file(tmp)
        final = table_dir / f"part-{index:04d}-{digest[:16]}.jsonl"
        tmp.replace(final)
        partitions.append({"partition_index": index, "path": str(final.resolve()), "rows": int(rows), "sha256": digest, "bytes": final.stat().st_size})
    return partitions


def _promote_work(output: Path, work_dir: Path, manifest: dict[str, Any], manifest_path: Path) -> None:
    final_table = output / "listing_restrictions"
    final_quarantine = output / "quarantine"
    if final_table.exists():
        shutil.rmtree(final_table)
    if final_quarantine.exists():
        shutil.rmtree(final_quarantine)
    shutil.move(str(work_dir / "listing_restrictions"), str(final_table))
    shutil.move(str(work_dir / "quarantine"), str(final_quarantine))
    _rewrite_paths_after_promotion(manifest, work_dir, output)
    _write_atomic_json(manifest_path, manifest)
    shutil.rmtree(work_dir, ignore_errors=True)


def _rewrite_paths_after_promotion(payload: Any, old_root: Path, new_root: Path) -> None:
    if isinstance(payload, dict):
        for key, value in list(payload.items()):
            if isinstance(value, str) and str(old_root.resolve()) in value:
                payload[key] = value.replace(str(old_root.resolve()), str(new_root.resolve()))
            else:
                _rewrite_paths_after_promotion(value, old_root, new_root)
    elif isinstance(payload, list):
        for value in payload:
            _rewrite_paths_after_promotion(value, old_root, new_root)


def _read_header(path: Path) -> list[str]:
    with _open_text(path) as handle:
        reader = csv.reader(handle)
        return next(reader)


def _official_listing_source(source_hash: str, source_bytes: int) -> dict[str, Any]:
    expected = OFFICIAL_FULL_SOURCE_EXPECTATIONS["anon_bo_lists"]
    return {
        "schema_version": "nber_official_listing_source_contract.v1",
        "expected_sha256": expected["sha256"],
        "actual_sha256": source_hash,
        "sha256_matches": source_hash == expected["sha256"],
        "expected_bytes": expected["bytes"],
        "actual_bytes": source_bytes,
        "bytes_match": source_bytes == expected["bytes"],
        "matches_expected_official_source": source_hash == expected["sha256"] and source_bytes == expected["bytes"],
        "research_only": True,
        "production_export_allowed": False,
    }


def _price_bucket(value: float) -> str:
    if value < 10:
        return "0_to_10"
    if value < 25:
        return "10_to_25"
    if value < 50:
        return "25_to_50"
    if value < 100:
        return "50_to_100"
    if value < 250:
        return "100_to_250"
    if value < 500:
        return "250_to_500"
    if value <= 1000:
        return "500_to_1000"
    return "over_1000"


def _bucket_for(text: str, bucket_count: int) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16) % bucket_count


def _hash_identifier(value: str | None) -> str | None:
    if value in {None, ""}:
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest().upper()


def _row_hash(row: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest().upper()


def _listing_row_hash(row: dict[str, str]) -> str:
    payload = "\x1f".join(str(row.get(column, "")) for column in REAL_LISTING_COLUMNS)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()


def _sha256_if_nonempty(path: Path) -> str | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    return sha256_file(path)


def _line_count(path: Path) -> int:
    rows = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows += 1
    return rows


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, newline="\n") as handle:
        temp = Path(handle.name)
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp, path)
