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
from typing import Any

from behavior_lab.datasets.nber_best_offer.full_listing_pass import FINAL_CONTRACT_VERSION
from behavior_lab.datasets.nber_best_offer.real_normalize import _find_source, _float_or_none, _int_or_none, _open_text, _parse_datetime_string
from behavior_lab.datasets.nber_best_offer.source_schema import sha256_file, validate_real_headers


THREAD_RESTRICTION_FORENSICS_VERSION = "nber_thread_restriction_forensics.v1"
FINAL_THREAD_TARGETS = {
    "T2_buyer_violation_listing_count": 3518,
    "T2_seller_violation_listing_count": 0,
    "T3_violation_listing_count": 1451,
    "T4_violation_listing_count": 1109,
    "T5_violation_listing_count": 4273,
}
WORKING_PAPER_THREAD_TARGETS = {
    "T2_buyer_violation_listing_count": 3529,
    "T3_violation_listing_count": 1453,
    "T4_violation_listing_count": 1111,
}


class RestrictionForensicsError(ValueError):
    pass


def default_output_dir() -> Path:
    return Path(os.environ.get("OFFERLAB_DATA_ROOT", r"C:\OfferLabData")) / "processed" / "nber_best_offer_full" / "thread_restriction_forensics"


def build_thread_restriction_forensics(
    raw_dir: str | Path,
    output_dir: str | Path | None = None,
    *,
    bucket_count: int = 128,
    resume: bool = True,
    drop_complete_duplicates: bool = True,
) -> dict[str, Any]:
    if bucket_count <= 0:
        raise RestrictionForensicsError("bucket_count must be positive")
    start = time.perf_counter()
    raw = Path(raw_dir)
    output = Path(output_dir) if output_dir is not None else default_output_dir()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    threads_path = _find_source(raw, "anon_bo_threads.csv")
    source_hash = sha256_file(threads_path)
    source_bytes = threads_path.stat().st_size
    header = _read_header(threads_path)
    header_validation = validate_real_headers(threads=header)
    if not header_validation["valid"]:
        raise RestrictionForensicsError(json.dumps(header_validation, sort_keys=True))
    signature = {
        "schema_version": "nber_thread_restriction_forensics_signature.v1",
        "forensics_version": THREAD_RESTRICTION_FORENSICS_VERSION,
        "contract_version": FINAL_CONTRACT_VERSION,
        "raw_dir": str(raw.resolve()),
        "source_path": str(threads_path.resolve()),
        "source_sha256": source_hash,
        "source_bytes": source_bytes,
        "bucket_count": bucket_count,
        "drop_complete_duplicates": drop_complete_duplicates,
        "header_validation": header_validation,
    }
    if resume and manifest_path.exists():
        current = _load_json(manifest_path)
        if current and current.get("signature") == signature and inspect_thread_restriction_forensics(output)["valid"]:
            current["idempotent_rerun"] = True
            return current

    work_dir = output / "_work"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    bucket_dir = work_dir / "thread_buckets"
    bucket_dir.mkdir()
    dedupe_db_path = work_dir / "complete_duplicate_rows.sqlite"
    db_path = work_dir / "restriction_forensics.sqlite"
    report_path = work_dir / "listing_thread_flags.jsonl"
    malformed_path = work_dir / "malformed_threads.jsonl"

    bucket_manifest = _bucket_thread_rows(
        threads_path,
        bucket_dir,
        malformed_path,
        dedupe_db_path=dedupe_db_path,
        bucket_count=bucket_count,
        drop_complete_duplicates=drop_complete_duplicates,
    )
    conn = sqlite3.connect(db_path)
    try:
        _configure_sqlite(conn)
        stats = _process_buckets(conn, bucket_dir, bucket_count=bucket_count)
        listing_counts = _listing_counts(conn)
        _write_listing_flags(conn, report_path)
    finally:
        conn.close()

    observed = {**listing_counts, **stats["violating_thread_counts"]}
    final_comparison = _target_comparison(listing_counts, FINAL_THREAD_TARGETS)
    working_paper_detection = _target_comparison(listing_counts, WORKING_PAPER_THREAD_TARGETS)
    manifest = {
        "schema_version": THREAD_RESTRICTION_FORENSICS_VERSION,
        "status": "complete",
        "signature": signature,
        "source_files": {
            "anon_bo_threads": {
                "path": str(threads_path.resolve()),
                "sha256": source_hash,
                "bytes": source_bytes,
            }
        },
        "header_validation": header_validation,
        "ordering": {
            "primary": ["anon_item_id", "anon_byr_id", "src_cre_date"],
            "secondary": "source_row_ordinal",
            "source_code_reference": "paper_sample.do: sort anon_item_id anon_byr_id src_cre_date",
        },
        "semantics": {
            "listing_level_propagation": True,
            "complete_duplicate_policy": "When enabled, match load_csv_files.do by dropping complete duplicate thread records before paper_sample.do.",
            "T2": "Count buyer price proposals where offr_type_id in {0,1}; count seller proposals where offr_type_id == 2; more than three invalidates the listing.",
            "T3": "A status_id == 7 countered event must be followed by the required counterparty counter type.",
            "T4": "A status_id in {1,9} accepted event must be final in the listing-buyer thread.",
            "T5": "Released paper_sample.do tags duplicate anon_item_id + anon_byr_id + src_cre_date after load_csv_files.do complete-record duplicate drop; timestamp-level duplicates are not deleted before T5.",
        },
        "bucket_manifest": bucket_manifest,
        "observed": observed,
        "final_target_comparison": final_comparison,
        "working_paper_definition_detection": {
            "matches_working_paper_counts": working_paper_detection["passed"],
            "comparison": working_paper_detection,
        },
        "ambiguous_cases": stats["ambiguous_cases"],
        "false_positive_samples": [],
        "false_negative_samples": [],
        "artifacts": {
            "sqlite": {"path": str((output / "restriction_forensics.sqlite").resolve())},
            "listing_thread_flags": {"path": str((output / "listing_thread_flags.jsonl").resolve()), "rows": listing_counts["listing_count_with_any_thread"]},
            "malformed_threads": {"path": str((output / "malformed_threads.jsonl").resolve()), "rows": bucket_manifest["malformed_rows"]},
        },
        "runtime_seconds": round(time.perf_counter() - start, 3),
    }
    _promote_work(output, work_dir, manifest)
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def inspect_thread_restriction_forensics(output_dir: str | Path | None = None) -> dict[str, Any]:
    output = Path(output_dir) if output_dir is not None else default_output_dir()
    manifest_path = output / "manifest.json"
    if not manifest_path.exists():
        return {"schema_version": "nber_thread_restriction_forensics_inspection.v1", "valid": False, "failures": ["missing_manifest"]}
    manifest = _load_json(manifest_path)
    if not manifest:
        return {"schema_version": "nber_thread_restriction_forensics_inspection.v1", "valid": False, "failures": ["invalid_manifest"]}
    failures = []
    for name in ["sqlite", "listing_thread_flags", "malformed_threads"]:
        path_text = manifest.get("artifacts", {}).get(name, {}).get("path")
        if not path_text:
            failures.append(f"{name}:missing_path")
        elif not Path(path_text).exists():
            failures.append(f"{name}:missing_file")
    source = manifest.get("source_files", {}).get("anon_bo_threads", {})
    source_path = Path(source.get("path", ""))
    if not source_path.exists():
        failures.append("source:missing_file")
    elif sha256_file(source_path) != source.get("sha256"):
        failures.append("source:sha256_mismatch")
    return {
        "schema_version": "nber_thread_restriction_forensics_inspection.v1",
        "valid": not failures,
        "failures": failures,
        "output_dir": str(output.resolve()),
        "manifest_sha256": sha256_file(manifest_path),
        "observed": manifest.get("observed", {}),
        "final_target_comparison": manifest.get("final_target_comparison", {}),
    }


def evaluate_thread_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (_sort_time(row.get("src_cre_date")), int(row.get("source_row_ordinal", 0))))
    offer_types = [_int_or_none_text(row.get("offr_type_id")) for row in ordered]
    statuses = [_int_or_none_text(row.get("status_id")) for row in ordered]
    times = [str(row.get("src_cre_date", "")) for row in ordered]
    buyer_offer_count = sum(1 for offer_type in offer_types if offer_type in {0, 1})
    seller_offer_count = sum(1 for offer_type in offer_types if offer_type == 2)
    t3 = False
    t4 = False
    ambiguous = []
    for index, (offer_type, status) in enumerate(zip(offer_types, statuses, strict=True)):
        next_type = offer_types[index + 1] if index + 1 < len(offer_types) else None
        if status == 7:
            if offer_type in {0, 1} and next_type != 2:
                t3 = True
            elif offer_type == 2 and next_type != 1:
                t3 = True
            elif offer_type is None:
                ambiguous.append("countered_event_missing_offer_type")
        if status in {1, 9} and index != len(offer_types) - 1:
            t4 = True
        if times[index] == "":
            ambiguous.append("missing_src_cre_date")
    return {
        "buyer_offer_count": buyer_offer_count,
        "seller_offer_count": seller_offer_count,
        "T2_buyer_violation": buyer_offer_count > 3,
        "T2_seller_violation": seller_offer_count > 3,
        "T3_violation": t3,
        "T4_violation": t4,
        "T5_violation": len(set(times)) != len(times),
        "ambiguous_reasons": sorted(set(ambiguous)),
    }


def _bucket_thread_rows(
    threads_path: Path,
    bucket_dir: Path,
    malformed_path: Path,
    *,
    dedupe_db_path: Path,
    bucket_count: int,
    drop_complete_duplicates: bool,
) -> dict[str, Any]:
    handles = [(bucket_dir / f"bucket_{index:04d}.tsv").open("w", encoding="utf-8", newline="\n") for index in range(bucket_count)]
    bucket_rows = [0 for _ in range(bucket_count)]
    accepted_rows = 0
    malformed_rows = 0
    duplicate_rows_removed = 0
    dedupe_conn = _open_dedupe_db(dedupe_db_path) if drop_complete_duplicates else None
    with malformed_path.open("w", encoding="utf-8", newline="\n") as malformed:
        try:
            with _open_text(threads_path) as handle:
                reader = csv.DictReader(handle)
                writer_handles = [csv.writer(handle, delimiter="\t", lineterminator="\n") for handle in handles]
                for ordinal, row in enumerate(reader, start=1):
                    if dedupe_conn is not None:
                        row_hash = _row_hash(row)
                        cursor = dedupe_conn.execute("INSERT OR IGNORE INTO row_hashes VALUES (?)", (row_hash,))
                        if cursor.rowcount == 0:
                            duplicate_rows_removed += 1
                            continue
                        if (accepted_rows + duplicate_rows_removed) % 100_000 == 0:
                            dedupe_conn.commit()
                    listing_id = str(row.get("anon_item_id", "")).strip()
                    buyer_id = str(row.get("anon_byr_id", "")).strip()
                    if not listing_id or not buyer_id:
                        malformed_rows += 1
                        if malformed_rows <= 100:
                            malformed.write(json.dumps({"line_number": ordinal + 1, "row_hash": _row_hash(row), "reason": "missing_listing_or_buyer_id"}, sort_keys=True) + "\n")
                        continue
                    bucket = _bucket_for(listing_id + "\0" + buyer_id, bucket_count)
                    writer_handles[bucket].writerow(
                        [
                            listing_id,
                            buyer_id,
                            _parse_datetime_string(row.get("src_cre_date")) or str(row.get("src_cre_date", "")),
                            ordinal,
                            row.get("offr_type_id", ""),
                            row.get("status_id", ""),
                            row.get("offr_price", ""),
                        ]
                    )
                    bucket_rows[bucket] += 1
                    accepted_rows += 1
        finally:
            if dedupe_conn is not None:
                dedupe_conn.commit()
                dedupe_conn.close()
            for handle in handles:
                handle.close()
    return {
        "valid": True,
        "bucket_count": bucket_count,
        "accepted_rows": accepted_rows,
        "malformed_rows": malformed_rows,
        "complete_duplicate_rows_removed": duplicate_rows_removed,
        "drop_complete_duplicates": drop_complete_duplicates,
        "buckets": [
            {
                "name": f"bucket_{index:04d}.tsv",
                "rows": bucket_rows[index],
                "sha256": sha256_file(bucket_dir / f"bucket_{index:04d}.tsv"),
            }
            for index in range(bucket_count)
        ],
    }


def _configure_sqlite(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=FILE")
    conn.executescript(
        """
        CREATE TABLE listing_thread_flags (
            listing_id TEXT PRIMARY KEY,
            T2_buyer_violation INTEGER NOT NULL,
            T2_seller_violation INTEGER NOT NULL,
            T3_violation INTEGER NOT NULL,
            T4_violation INTEGER NOT NULL,
            T5_violation INTEGER NOT NULL,
            violating_thread_count INTEGER NOT NULL,
            thread_count INTEGER NOT NULL
        ) WITHOUT ROWID;
        """
    )


def _open_dedupe_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("CREATE TABLE row_hashes (row_hash TEXT PRIMARY KEY) WITHOUT ROWID")
    return conn


def _process_buckets(conn: sqlite3.Connection, bucket_dir: Path, *, bucket_count: int) -> dict[str, Any]:
    thread_counts = {
        "T2_buyer_violation_thread_count": 0,
        "T2_seller_violation_thread_count": 0,
        "T3_violation_thread_count": 0,
        "T4_violation_thread_count": 0,
        "T5_violation_thread_count": 0,
    }
    ambiguous_cases: list[dict[str, Any]] = []
    for index in range(bucket_count):
        rows = _read_bucket(bucket_dir / f"bucket_{index:04d}.tsv")
        rows.sort(key=lambda row: (row["listing_id"], row["buyer_id"], row["sort_time"], row["source_row_ordinal"]))
        current_key = None
        group: list[dict[str, Any]] = []
        for row in rows:
            key = (row["listing_id"], row["buyer_id"])
            if current_key is not None and key != current_key:
                _consume_group(conn, current_key, group, thread_counts, ambiguous_cases)
                group = []
            current_key = key
            group.append(row)
        if current_key is not None:
            _consume_group(conn, current_key, group, thread_counts, ambiguous_cases)
        conn.commit()
    return {"violating_thread_counts": thread_counts, "ambiguous_cases": ambiguous_cases[:100]}


def _consume_group(
    conn: sqlite3.Connection,
    key: tuple[str, str],
    rows: list[dict[str, Any]],
    thread_counts: dict[str, int],
    ambiguous_cases: list[dict[str, Any]],
) -> None:
    listing_id, buyer_id = key
    evaluation = evaluate_thread_rows(rows)
    flags = {
        "T2_buyer_violation": bool(evaluation["T2_buyer_violation"]),
        "T2_seller_violation": bool(evaluation["T2_seller_violation"]),
        "T3_violation": bool(evaluation["T3_violation"]),
        "T4_violation": bool(evaluation["T4_violation"]),
        "T5_violation": bool(evaluation["T5_violation"]),
    }
    for flag, value in flags.items():
        if value:
            thread_counts[f"{flag}_thread_count"] += 1
    if evaluation["ambiguous_reasons"] and len(ambiguous_cases) < 100:
        ambiguous_cases.append(
            {
                "listing_id_hash": _hash_text(listing_id),
                "buyer_id_hash": _hash_text(buyer_id),
                "reasons": evaluation["ambiguous_reasons"],
                "row_count": len(rows),
            }
        )
    conn.execute(
        """
        INSERT INTO listing_thread_flags VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(listing_id) DO UPDATE SET
            T2_buyer_violation = MAX(listing_thread_flags.T2_buyer_violation, excluded.T2_buyer_violation),
            T2_seller_violation = MAX(listing_thread_flags.T2_seller_violation, excluded.T2_seller_violation),
            T3_violation = MAX(listing_thread_flags.T3_violation, excluded.T3_violation),
            T4_violation = MAX(listing_thread_flags.T4_violation, excluded.T4_violation),
            T5_violation = MAX(listing_thread_flags.T5_violation, excluded.T5_violation),
            violating_thread_count = listing_thread_flags.violating_thread_count + excluded.violating_thread_count,
            thread_count = listing_thread_flags.thread_count + excluded.thread_count
        """,
        (
            listing_id,
            int(flags["T2_buyer_violation"]),
            int(flags["T2_seller_violation"]),
            int(flags["T3_violation"]),
            int(flags["T4_violation"]),
            int(flags["T5_violation"]),
            int(any(flags.values())),
            1,
        ),
    )


def _listing_counts(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS listing_count_with_any_thread,
            SUM(T2_buyer_violation) AS T2_buyer_violation_listing_count,
            SUM(T2_seller_violation) AS T2_seller_violation_listing_count,
            SUM(T3_violation) AS T3_violation_listing_count,
            SUM(T4_violation) AS T4_violation_listing_count,
            SUM(T5_violation) AS T5_violation_listing_count
        FROM listing_thread_flags
        """
    ).fetchone()
    columns = [
        "listing_count_with_any_thread",
        "T2_buyer_violation_listing_count",
        "T2_seller_violation_listing_count",
        "T3_violation_listing_count",
        "T4_violation_listing_count",
        "T5_violation_listing_count",
    ]
    return {column: int(value or 0) for column, value in zip(columns, row, strict=True)}


def _write_listing_flags(conn: sqlite3.Connection, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in conn.execute(
            """
            SELECT listing_id, T2_buyer_violation, T2_seller_violation, T3_violation, T4_violation, T5_violation, violating_thread_count, thread_count
            FROM listing_thread_flags
            ORDER BY listing_id
            """
        ):
            handle.write(
                json.dumps(
                    {
                        "listing_id": row[0],
                        "T1_violation": None,
                        "T2_buyer_violation": bool(row[1]),
                        "T2_seller_violation": bool(row[2]),
                        "T3_violation": bool(row[3]),
                        "T4_violation": bool(row[4]),
                        "T5_violation": bool(row[5]),
                        "violating_thread_count": int(row[6]),
                        "thread_count": int(row[7]),
                        "restriction_contract_version": FINAL_CONTRACT_VERSION,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )


def _read_bucket(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        for listing_id, buyer_id, sort_time, ordinal, offer_type, status, price in reader:
            rows.append(
                {
                    "listing_id": listing_id,
                    "buyer_id": buyer_id,
                    "sort_time": sort_time,
                    "source_row_ordinal": int(ordinal),
                    "src_cre_date": sort_time,
                    "offr_type_id": offer_type,
                    "status_id": status,
                    "offr_price": price,
                }
            )
    return rows


def _target_comparison(observed: dict[str, int], expected: dict[str, int]) -> dict[str, Any]:
    rows = []
    for key, expected_value in expected.items():
        observed_value = observed.get(key)
        rows.append({"target": key, "expected": expected_value, "observed": observed_value, "passed": observed_value == expected_value})
    return {"passed": all(row["passed"] for row in rows), "targets": rows}


def _promote_work(output: Path, work_dir: Path, manifest: dict[str, Any]) -> None:
    for name in ["restriction_forensics.sqlite", "listing_thread_flags.jsonl", "malformed_threads.jsonl"]:
        target = output / name
        if target.exists():
            target.unlink()
        shutil.move(str(work_dir / name), str(target))
    _rewrite_paths_after_promotion(manifest, work_dir, output)
    _write_atomic_json(output / "manifest.json", manifest)
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
        return next(csv.reader(handle))


def _sort_time(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _int_or_none_text(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    return int(float(value))


def _bucket_for(text: str, bucket_count: int) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16) % bucket_count


def _row_hash(row: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest().upper()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16].upper()


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, newline="\n") as handle:
        temp = Path(handle.name)
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp, path)
