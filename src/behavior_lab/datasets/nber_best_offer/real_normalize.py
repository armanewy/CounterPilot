from __future__ import annotations

from collections import Counter, defaultdict
import csv
from dataclasses import asdict, dataclass
from datetime import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any, Iterable, Iterator

from behavior_lab.datasets.nber_best_offer.source_schema import (
    OFFER_TYPE_MAP,
    REAL_LISTING_COLUMNS,
    REAL_THREAD_COLUMNS,
    REAL_TRANSFORMATION_VERSION,
    STATUS_MAP,
    mapping_hash,
    sha256_file,
    validate_real_headers,
)


class NberRealNormalizeError(ValueError):
    pass


@dataclass(frozen=True)
class Quarantine:
    counts: dict[str, int]
    examples: list[dict[str, Any]]

    def add(self, reason: str, row: dict[str, str], *, source_file: str, line_number: int) -> None:
        self.counts[reason] = self.counts.get(reason, 0) + 1
        if len(self.examples) < 25:
            self.examples.append(
                {
                    "source_file": source_file,
                    "line_number": line_number,
                    "reason": reason,
                    "row_hash": _row_hash(row),
                    "fields": sorted(row),
                }
            )


def normalize_real_dataset(
    raw_dir: str | Path,
    output_dir: str | Path,
    *,
    limit_threads: int | None = None,
    full: bool = False,
    bucket_count: int = 32,
    partition_rows: int = 50_000,
    seed: int = 20240621,
    stop_after_thread_pass: bool = False,
) -> dict[str, Any]:
    if not full and limit_threads is None:
        raise NberRealNormalizeError("Use --limit-threads or --full for real NBER normalization")
    start = time.perf_counter()
    raw = Path(raw_dir)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    checkpoints = output / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    temp_dir = output / "_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    tables = output / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    args_signature = {
        "raw_dir": str(raw.resolve()),
        "limit_threads": limit_threads,
        "full": full,
        "bucket_count": bucket_count,
        "partition_rows": partition_rows,
        "seed": seed,
        "transformation_version": REAL_TRANSFORMATION_VERSION,
    }
    if manifest_path.exists():
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
        if current.get("command_args") == args_signature and current.get("status") == "complete":
            current["idempotent_rerun"] = True
            return current

    lists_path = _find_source(raw, "anon_bo_lists.csv")
    threads_path = _find_source(raw, "anon_bo_threads.csv")
    quarantine = Quarantine(counts={}, examples=[])
    source_hashes = {"anon_bo_lists": sha256_file(lists_path), "anon_bo_threads": sha256_file(threads_path)}
    header_report = _validate_source_headers(lists_path, threads_path)
    if not header_report["valid"]:
        raise NberRealNormalizeError(json.dumps(header_report, sort_keys=True))

    bucket_dir = temp_dir / "thread_buckets"
    ids_path = temp_dir / "thread_listing_ids.jsonl"
    thread_checkpoint = checkpoints / "thread_pass.complete.json"
    thread_counts: dict[str, Any]
    if thread_checkpoint.exists():
        thread_counts = json.loads(thread_checkpoint.read_text(encoding="utf-8"))
    else:
        if bucket_dir.exists():
            shutil.rmtree(bucket_dir)
        bucket_dir.mkdir(parents=True)
        thread_counts = _bucket_thread_rows(
            threads_path,
            bucket_dir,
            ids_path,
            bucket_count=bucket_count,
            limit_threads=limit_threads,
            quarantine=quarantine,
        )
        _write_atomic_json(thread_checkpoint, thread_counts)
    if stop_after_thread_pass:
        return {"status": "stopped_after_thread_pass", "thread_pass": thread_counts, "output_dir": str(output.resolve())}

    turn_table = tables / "negotiation_turns"
    listing_table = tables / "listings"
    for table_dir in [turn_table, listing_table]:
        if table_dir.exists():
            shutil.rmtree(table_dir)
        table_dir.mkdir(parents=True)

    turn_rows = _write_turn_partitions(bucket_dir, turn_table, partition_rows=partition_rows, quarantine=quarantine)
    listing_ids = _load_listing_ids(ids_path)
    listing_rows = _write_listing_partitions(lists_path, listing_ids, listing_table, partition_rows=partition_rows, quarantine=quarantine)
    unmatched = sorted(listing_ids - set(listing_rows["matched_listing_ids"]))
    quarantine_path = output / "quarantine.json"
    quarantine_payload = {"counts": quarantine.counts, "examples": quarantine.examples}
    _write_atomic_json(quarantine_path, quarantine_payload)
    manifest = {
        "status": "complete",
        "schema_version": "nber_real_normalized_manifest.v1",
        "transformation_version": REAL_TRANSFORMATION_VERSION,
        "git_commit": _git_commit(),
        "command_args": args_signature,
        "random_seed": seed,
        "mapping_manifest_hash": mapping_hash(),
        "source_files": {
            "anon_bo_lists": {"path": str(lists_path.resolve()), "sha256": source_hashes["anon_bo_lists"], "bytes": lists_path.stat().st_size},
            "anon_bo_threads": {"path": str(threads_path.resolve()), "sha256": source_hashes["anon_bo_threads"], "bytes": threads_path.stat().st_size},
        },
        "header_validation": header_report,
        "tables": {
            "negotiation_turns": {"path": str(turn_table.resolve()), "format": "parquet" if _pyarrow_available() else "jsonl", "rows": turn_rows["rows"], "partitions": turn_rows["partitions"]},
            "listings": {"path": str(listing_table.resolve()), "format": "parquet" if _pyarrow_available() else "jsonl", "rows": listing_rows["rows"], "partitions": listing_rows["partitions"]},
        },
        "source_thread_pass": thread_counts,
        "thread_linked_listing_extraction": {
            "distinct_listing_ids": len(listing_ids),
            "matched_listings": listing_rows["rows"],
            "unmatched_listing_ids": len(unmatched),
            "unmatched_examples_hash": [_hash_value(value) for value in unmatched[:25]],
            "non_negotiated_listings_omitted": True,
        },
        "quarantine": {"path": str(quarantine_path.resolve()), **quarantine_payload},
        "lineage": {
            "raw_source_hashes": source_hashes,
            "split_manifest_hash": None,
            "normalization_manifest_hash": None,
        },
        "runtime_seconds": round(time.perf_counter() - start, 3),
    }
    _write_atomic_json(manifest_path, manifest)
    manifest["lineage"]["normalization_manifest_hash"] = sha256_file(manifest_path)
    _write_atomic_json(manifest_path, manifest)
    return manifest


def inspect_real_source_schema(raw_dir: str | Path) -> dict[str, Any]:
    raw = Path(raw_dir)
    lists_path = _find_source(raw, "anon_bo_lists.csv")
    threads_path = _find_source(raw, "anon_bo_threads.csv")
    return _validate_source_headers(lists_path, threads_path)


def _bucket_thread_rows(
    threads_path: Path,
    bucket_dir: Path,
    ids_path: Path,
    *,
    bucket_count: int,
    limit_threads: int | None,
    quarantine: Quarantine,
) -> dict[str, Any]:
    seen_threads: set[str] = set()
    accepted_rows = 0
    duplicate_rows = 0
    row_hashes: set[str] = set()
    status_counts: Counter[str] = Counter()
    offer_type_counts: Counter[str] = Counter()
    bucket_handles = [(bucket_dir / f"bucket_{index:04d}.jsonl").open("w", encoding="utf-8", newline="\n") for index in range(bucket_count)]
    try:
        with _open_text(threads_path) as handle, ids_path.open("w", encoding="utf-8", newline="\n") as ids:
            reader = csv.DictReader(handle)
            for line_number, row in enumerate(reader, start=2):
                thread_id = row.get("anon_thread_id", "")
                listing_id = row.get("anon_item_id", "")
                if not thread_id or not listing_id or not row.get("anon_byr_id") or not row.get("anon_slr_id"):
                    quarantine.add("missing_required_thread_identifier", row, source_file=threads_path.name, line_number=line_number)
                    continue
                if limit_threads is not None and thread_id not in seen_threads and len(seen_threads) >= limit_threads:
                    continue
                row_digest = _row_hash(row)
                if row_digest in row_hashes:
                    duplicate_rows += 1
                    continue
                row_hashes.add(row_digest)
                seen_threads.add(thread_id)
                status_counts[row.get("status_id", "")] += 1
                offer_type_counts[row.get("offr_type_id", "")] += 1
                if row.get("status_id", "") not in STATUS_MAP:
                    quarantine.add("unknown_status_id", row, source_file=threads_path.name, line_number=line_number)
                    continue
                if row.get("offr_type_id", "") not in OFFER_TYPE_MAP:
                    quarantine.add("unknown_offr_type_id", row, source_file=threads_path.name, line_number=line_number)
                    continue
                bucket = int(hashlib.sha256(thread_id.encode("utf-8")).hexdigest(), 16) % bucket_count
                bucket_handles[bucket].write(json.dumps(row, sort_keys=True) + "\n")
                ids.write(json.dumps({"listing_id": listing_id}, sort_keys=True) + "\n")
                accepted_rows += 1
    finally:
        for handle in bucket_handles:
            handle.close()
    return {
        "source": str(threads_path.resolve()),
        "accepted_rows": accepted_rows,
        "distinct_threads": len(seen_threads),
        "duplicate_full_rows_removed": duplicate_rows,
        "status_counts": dict(status_counts),
        "offer_type_counts": dict(offer_type_counts),
        "limit_threads": limit_threads,
    }


def _write_turn_partitions(bucket_dir: Path, table_dir: Path, *, partition_rows: int, quarantine: Quarantine) -> dict[str, Any]:
    rows_out = []
    partitions = []
    total = 0
    part_index = 0
    for bucket in sorted(bucket_dir.glob("bucket_*.jsonl")):
        grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
        for line in bucket.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                grouped[str(row["anon_thread_id"])].append(row)
        for thread_id, rows in grouped.items():
            rows.sort(key=lambda item: (_parse_datetime(item.get("src_cre_date", "")) or datetime.max, item.get("offr_type_id", ""), item.get("offr_price", "")))
            for index, row in enumerate(rows, start=1):
                try:
                    rows_out.append(_normalize_thread_row(row, turn_index=index))
                except Exception:
                    quarantine.add("thread_normalization_error", row, source_file=bucket.name, line_number=index)
                    continue
                if len(rows_out) >= partition_rows:
                    partitions.append(_write_partition(table_dir, "turns", part_index, rows_out))
                    total += len(rows_out)
                    rows_out = []
                    part_index += 1
    if rows_out:
        partitions.append(_write_partition(table_dir, "turns", part_index, rows_out))
        total += len(rows_out)
    return {"rows": total, "partitions": partitions}


def _write_listing_partitions(
    lists_path: Path,
    listing_ids: set[str],
    table_dir: Path,
    *,
    partition_rows: int,
    quarantine: Quarantine,
) -> dict[str, Any]:
    rows_out = []
    partitions = []
    total = 0
    part_index = 0
    matched: set[str] = set()
    with _open_text(lists_path) as handle:
        reader = csv.DictReader(handle)
        for line_number, row in enumerate(reader, start=2):
            listing_id = row.get("anon_item_id", "")
            if listing_id not in listing_ids:
                continue
            try:
                rows_out.append(_normalize_listing_row(row))
            except Exception:
                quarantine.add("listing_normalization_error", row, source_file=lists_path.name, line_number=line_number)
                continue
            matched.add(listing_id)
            if len(rows_out) >= partition_rows:
                partitions.append(_write_partition(table_dir, "listings", part_index, rows_out))
                total += len(rows_out)
                rows_out = []
                part_index += 1
    if rows_out:
        partitions.append(_write_partition(table_dir, "listings", part_index, rows_out))
        total += len(rows_out)
    return {"rows": total, "partitions": partitions, "matched_listing_ids": matched}


def _normalize_thread_row(row: dict[str, str], *, turn_index: int) -> dict[str, Any]:
    offer_type = OFFER_TYPE_MAP[row["offr_type_id"]]
    return {
        "source_row_id": _row_hash(row),
        "thread_id": row["anon_thread_id"],
        "listing_id": row["anon_item_id"],
        "buyer_id": row["anon_byr_id"],
        "seller_id": row["anon_slr_id"],
        "turn_index": turn_index,
        "actor": offer_type["actor"],
        "action": offer_type["action"],
        "amount": _float_or_none(row.get("offr_price")),
        "status": STATUS_MAP[row["status_id"]],
        "status_id": _int_or_none(row.get("status_id")),
        "event_date": _parse_date_string(row.get("src_cre_dt")),
        "event_time": _parse_datetime_string(row.get("src_cre_date")),
        "response_time": _parse_datetime_string(row.get("response_time")),
        "seller_feedback_score_at_offer": _int_or_none(row.get("fdbk_score_src")),
        "seller_feedback_positive_at_offer": _float_or_none(row.get("fdbk_pstv_src")),
        "seller_best_offer_thread_history": _int_or_none(row.get("slr_hist")),
        "buyer_best_offer_thread_history": _int_or_none(row.get("byr_hist")),
        "has_message": _bool_or_none(row.get("any_mssg")),
        "buyer_us": _bool_or_none(row.get("byr_us")),
        "transformation_version": REAL_TRANSFORMATION_VERSION,
    }


def _normalize_listing_row(row: dict[str, str]) -> dict[str, Any]:
    product_id = None if row.get("anon_product_id") in {"", "547957"} else row.get("anon_product_id")
    return {
        "source_row_id": row["anon_item_id"],
        "listing_id": row["anon_item_id"],
        "seller_id": row["anon_slr_id"],
        "buyer_id_if_sold": row.get("anon_buyer_id") or None,
        "title_code": row.get("anon_title_code") or None,
        "product_id": product_id,
        "category": row.get("anon_leaf_categ_id") or None,
        "meta_category": row.get("meta_categ_id") or None,
        "condition": row.get("item_cndtn_id") or None,
        "listing_price": _float_or_none(row.get("start_price_usd")),
        "reference_price": _float_or_none(row.get("ref_price4")),
        "reference_count": _int_or_none(row.get("count4")),
        "start_time": _parse_date_string(row.get("auct_start_dt")),
        "end_time": _parse_date_string(row.get("auct_end_dt")),
        "final_sale_price": _float_or_none(row.get("item_price")),
        "sold_by_best_offer": _bool_or_none(row.get("bo_ck_yn")),
        "photo_count": _int_or_none(row.get("photo_count")),
        "view_count": _int_or_none(row.get("view_item_count")),
        "watcher_count": _int_or_none(row.get("wtchr_count")),
        "auto_decline_price": _float_or_none(row.get("decline_price")),
        "auto_accept_price": _float_or_none(row.get("accept_price")),
        "seller_us": _bool_or_none(row.get("slr_us")),
        "buyer_us_if_sold": _bool_or_none(row.get("buyer_us")),
        "transformation_version": REAL_TRANSFORMATION_VERSION,
    }


def _write_partition(table_dir: Path, stem: str, index: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if _pyarrow_available():
        import pyarrow as pa
        import pyarrow.parquet as pq

        path = table_dir / f"{stem}_{index:05d}.parquet"
        table = pa.Table.from_pylist(rows)
        tmp = path.with_suffix(path.suffix + ".tmp")
        pq.write_table(table, tmp)
        os.replace(tmp, path)
    else:
        path = table_dir / f"{stem}_{index:05d}.jsonl"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=table_dir, newline="\n") as handle:
            tmp_path = Path(handle.name)
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        os.replace(tmp_path, path)
    return {"path": str(path.resolve()), "rows": len(rows), "sha256": sha256_file(path)}


def _validate_source_headers(lists_path: Path, threads_path: Path) -> dict[str, Any]:
    listing_header = _read_header(lists_path)
    thread_header = _read_header(threads_path)
    return validate_real_headers(listings=listing_header, threads=thread_header)


def _read_header(path: Path) -> list[str]:
    with _open_text(path) as handle:
        reader = csv.reader(handle)
        return next(reader)


def _find_source(root: Path, name: str) -> Path:
    candidates = [root / name, root / f"{name}.gz"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise NberRealNormalizeError(f"Missing {name} or {name}.gz in {root}")


def _load_listing_ids(path: Path) -> set[str]:
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            ids.add(str(json.loads(line)["listing_id"]))
    return ids


def _open_text(path: Path) -> Iterator[str]:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def _parse_date_string(value: str | None) -> str | None:
    parsed = _parse_date(value)
    return parsed.date().isoformat() if parsed else None


def _parse_datetime_string(value: str | None) -> str | None:
    parsed = _parse_datetime(value)
    return parsed.isoformat() if parsed else None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%d%b%Y")


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%d%b%Y %H:%M:%S")


def _float_or_none(value: str | None) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)


def _int_or_none(value: str | None) -> int | None:
    if value in {None, ""}:
        return None
    return int(float(value))


def _bool_or_none(value: str | None) -> bool | None:
    if value in {None, ""}:
        return None
    if value == "1":
        return True
    if value == "0":
        return False
    raise ValueError(f"Expected 0/1 boolean, got {value!r}")


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, newline="\n") as handle:
        temp = Path(handle.name)
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp, path)


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[4], text=True).strip()
    except Exception:
        return None


def _pyarrow_available() -> bool:
    try:
        import pyarrow  # noqa: F401
    except Exception:
        return False
    return True


def _row_hash(row: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True).encode("utf-8")).hexdigest()


def _hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
