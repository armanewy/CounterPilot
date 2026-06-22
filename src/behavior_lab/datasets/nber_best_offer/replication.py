from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from behavior_lab.datasets.nber_best_offer.real_normalize import (
    OFFICIAL_FULL_SOURCE_EXPECTATIONS,
    _find_source,
    _float_or_none,
    _int_or_none,
    _open_text,
    _parse_datetime_string,
)
from behavior_lab.datasets.nber_best_offer.source_schema import default_mapping_path, load_real_mapping, mapping_hash, repo_root, sha256_file


FULL_REPLICATION_VERSION = "nber_full_replication_stream.v1"
FULL_REPLICATION_BUCKETS = 128


def default_targets_path() -> Path:
    return repo_root() / "datasets" / "manifests" / "nber_replication_targets.yaml"


def load_replication_targets(path: str | Path | None = None) -> dict[str, Any]:
    target_path = Path(path) if path is not None else default_targets_path()
    return json.loads(target_path.read_text(encoding="utf-8"))


def validate_replication_targets(path: str | Path | None = None) -> dict[str, Any]:
    targets = load_replication_targets(path)
    all_targets = _flatten_targets(targets)
    errors = []
    ids = set()
    for target in all_targets:
        target_id = target.get("id")
        if not target_id:
            errors.append("target missing id")
            continue
        if target_id in ids:
            errors.append(f"duplicate target id {target_id}")
        ids.add(target_id)
        for key in ["formula", "tolerance"]:
            if key not in target:
                errors.append(f"{target_id} missing {key}")
        if "fatal" not in target and "status" not in target:
            errors.append(f"{target_id} missing fatal/status")
        if "source" not in target and "source_refs" not in target:
            errors.append(f"{target_id} missing source/source_refs")
    level_counts: dict[str, int] = {}
    for target in all_targets:
        level = str(target.get("level", "unknown"))
        level_counts[level] = level_counts.get(level, 0) + 1
    return {
        "valid": not errors,
        "errors": errors,
        "target_count": len(all_targets),
        "level_counts": level_counts,
        "targets_hash": sha256_file(Path(path) if path is not None else default_targets_path()),
    }


def replication_check(normalized_dir: str | Path, targets_path: str | Path | None = None) -> dict[str, Any]:
    root = Path(normalized_dir)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing normalized manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    targets = load_replication_targets(targets_path)
    if _is_official_full_manifest(manifest):
        return _full_release_replication_check(root, manifest, targets, targets_path=targets_path)
    return _bounded_replication_check(root, manifest, targets, targets_path=targets_path)


def _bounded_replication_check(
    root: Path,
    manifest: dict[str, Any],
    targets: dict[str, Any],
    *,
    targets_path: str | Path | None,
) -> dict[str, Any]:
    mapping = load_real_mapping(default_mapping_path())
    results = []
    structural_summary = _structural_summary(manifest)
    for target in _flatten_targets(targets):
        target_id = target["id"]
        if target_id == "headers_lists_exact":
            passed = manifest.get("header_validation", {}).get("files", {}).get("anon_bo_lists.csv", {}).get("valid") is True
            results.append(_result(target, passed=passed, observed=passed))
        elif target_id == "headers_threads_exact":
            passed = manifest.get("header_validation", {}).get("files", {}).get("anon_bo_threads.csv", {}).get("valid") is True
            results.append(_result(target, passed=passed, observed=passed))
        elif target_id == "status_codes_known":
            known = set(mapping["code_maps"]["status_id"])
            observed = set(manifest.get("source_thread_pass", {}).get("status_counts", {}).keys())
            passed = not observed or observed <= known
            results.append(_result(target, passed=passed, observed=sorted(observed)))
        elif target_id == "offer_type_codes_known":
            known = set(mapping["code_maps"]["offr_type_id"])
            observed = set(manifest.get("source_thread_pass", {}).get("offer_type_counts", {}).keys())
            passed = not observed or observed <= known
            results.append(_result(target, passed=passed, observed=sorted(observed)))
        elif target_id == "thread_rows_have_thread_listing_buyer_seller":
            missing = manifest.get("quarantine", {}).get("counts", {}).get("missing_required_thread_identifier", 0)
            results.append(_result(target, passed=missing == 0, observed=missing))
        elif target_id.startswith("struct_"):
            observed = structural_summary.get(target_id, "not_evaluated_on_current_sample")
            results.append(_result(target, passed=None, observed=observed))
        else:
            results.append(_result(target, passed=None, observed="not_evaluated_on_current_sample"))
    return _replication_payload(root, manifest, results, targets_path=targets_path, scope="bounded_or_structural_smoke")


def _full_release_replication_check(
    root: Path,
    manifest: dict[str, Any],
    targets: dict[str, Any],
    *,
    targets_path: str | Path | None,
) -> dict[str, Any]:
    work_dir = root / "_replication"
    work_dir.mkdir(parents=True, exist_ok=True)
    target_path = Path(targets_path) if targets_path is not None else default_targets_path()
    signature = _full_replication_signature(root, manifest, target_path)
    summary_path = work_dir / "full_replication_summary.json"
    summary = _load_json(summary_path)
    if not summary or summary.get("signature") != signature:
        summary = _compute_full_replication_summary(root, manifest, work_dir, signature)
        _write_json(summary_path, summary)
    observed_targets = summary["observed_targets"]
    results = []
    for target in _flatten_targets(targets):
        target_id = target["id"]
        if target_id in observed_targets:
            observed = observed_targets[target_id]
            results.append(_result(target, passed=_target_passed(target, observed), observed=observed))
        else:
            results.append(_result(target, passed=None, observed="not_evaluated_by_full_replication_stream"))
    payload = _replication_payload(root, manifest, results, targets_path=targets_path, scope="official_full_release")
    payload["full_replication_artifact"] = {
        "path": str(summary_path.resolve()),
        "sha256": sha256_file(summary_path),
        "signature": signature,
        "runtime_seconds": summary.get("runtime_seconds"),
        "checkpoints": summary.get("checkpoints", {}),
        "observed_target_count": len(observed_targets),
    }
    payload["limitations"] = [
        "Replication reproduces the released Stata sample restrictions from CSV inputs with deterministic Python streaming.",
        "The artifact is public-research evidence only; it is not seller-specific profit or causal intervention evidence.",
        "Reference-price and game-tree diagnostics are reported when computed but are nonfatal unless promoted in the frozen target manifest.",
    ]
    return payload


def _compute_full_replication_summary(root: Path, manifest: dict[str, Any], work_dir: Path, signature: dict[str, Any]) -> dict[str, Any]:
    start = time.perf_counter()
    db_path = work_dir / "full_replication.sqlite"
    checkpoint_path = work_dir / "checkpoint.json"
    checkpoint = _load_json(checkpoint_path)
    if not checkpoint or checkpoint.get("signature") != signature:
        if db_path.exists():
            db_path.unlink()
        _write_json(checkpoint_path, {"signature": signature, "phase": "initialized", "updated_utc": _utc_now()})
        checkpoint = _load_json(checkpoint_path)
    conn = sqlite3.connect(db_path)
    try:
        _configure_sqlite(conn)
        phase = checkpoint.get("phase")
        if phase not in {"thread_groups_complete", "listings_loaded", "sample_table_complete", "summary_complete"}:
            _build_thread_group_tables(root, manifest, work_dir, conn)
            _write_json(checkpoint_path, {"signature": signature, "phase": "thread_groups_complete", "updated_utc": _utc_now()})
            phase = "thread_groups_complete"
        if phase == "thread_groups_complete":
            _load_listing_rows(manifest, conn)
            _write_json(checkpoint_path, {"signature": signature, "phase": "listings_loaded", "updated_utc": _utc_now()})
            phase = "listings_loaded"
        if phase == "listings_loaded":
            _build_listing_sample_table(conn)
            _write_json(checkpoint_path, {"signature": signature, "phase": "sample_table_complete", "updated_utc": _utc_now()})
            phase = "sample_table_complete"
        observed = _calculate_observed_targets(conn)
        summary = {
            "schema_version": FULL_REPLICATION_VERSION,
            "signature": signature,
            "observed_targets": observed,
            "checkpoints": {
                "thread_groups_complete": True,
                "listings_loaded": True,
                "sample_table_complete": True,
            },
            "database_path": str(db_path.resolve()),
            "generated_utc": _utc_now(),
            "runtime_seconds": round(time.perf_counter() - start, 3),
        }
        _write_json(checkpoint_path, {"signature": signature, "phase": "summary_complete", "updated_utc": _utc_now()})
        return summary
    finally:
        conn.close()


def _build_thread_group_tables(root: Path, manifest: dict[str, Any], work_dir: Path, conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS listing_thread_flags;
        DROP TABLE IF EXISTS buyer_offer_stats;
        DROP TABLE IF EXISTS thread_summaries;
        CREATE TABLE listing_thread_flags (
            listing_id TEXT PRIMARY KEY,
            has_thread INTEGER NOT NULL,
            max_offer REAL,
            crit_numoff_byr INTEGER NOT NULL,
            crit_numoff_slr INTEGER NOT NULL,
            crit_counter INTEGER NOT NULL,
            crit_accept INTEGER NOT NULL,
            crit_duplicate_time INTEGER NOT NULL,
            thread_group_count INTEGER NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE buyer_offer_stats (
            buyer_id TEXT PRIMARY KEY,
            num_offrs INTEGER NOT NULL,
            num_threads INTEGER NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE thread_summaries (
            listing_id TEXT NOT NULL,
            buyer_id TEXT NOT NULL,
            rounds INTEGER NOT NULL,
            success INTEGER NOT NULL,
            first_offer_price REAL,
            first_status INTEGER,
            first_offer_type INTEGER,
            first_slr_hist INTEGER,
            first_byr_hist INTEGER,
            offer_types TEXT NOT NULL,
            statuses TEXT NOT NULL,
            PRIMARY KEY (listing_id, buyer_id)
        ) WITHOUT ROWID;
        """
    )
    bucket_dir = work_dir / "thread_group_buckets"
    if bucket_dir.exists():
        shutil.rmtree(bucket_dir)
    bucket_dir.mkdir(parents=True)
    handles = [(bucket_dir / f"group_{index:03d}.tsv").open("w", encoding="utf-8", newline="\n") for index in range(FULL_REPLICATION_BUCKETS)]
    try:
        for row in _iter_deduped_thread_rows(root, manifest):
            listing_id = str(row.get("anon_item_id", ""))
            buyer_id = str(row.get("anon_byr_id", ""))
            if not listing_id or not buyer_id:
                continue
            sort_time = _parse_datetime_string(row.get("src_cre_date")) or str(row.get("src_cre_date", ""))
            offer_type = str(_int_or_none(row.get("offr_type_id")) if _int_or_none(row.get("offr_type_id")) is not None else "")
            status = str(_int_or_none(row.get("status_id")) if _int_or_none(row.get("status_id")) is not None else "")
            price = str(_float_or_none(row.get("offr_price")) if _float_or_none(row.get("offr_price")) is not None else "")
            slr_hist = str(_int_or_none(row.get("slr_hist")) if _int_or_none(row.get("slr_hist")) is not None else "")
            byr_hist = str(_int_or_none(row.get("byr_hist")) if _int_or_none(row.get("byr_hist")) is not None else "")
            bucket = _bucket_for(listing_id + "\0" + buyer_id, FULL_REPLICATION_BUCKETS)
            handles[bucket].write("\t".join([listing_id, buyer_id, sort_time, offer_type, status, price, slr_hist, byr_hist]) + "\n")
    finally:
        for handle in handles:
            handle.close()
    for bucket_path in sorted(bucket_dir.glob("group_*.tsv")):
        rows = []
        with bucket_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                listing_id, buyer_id, sort_time, offer_type, status, price, slr_hist, byr_hist = line.rstrip("\n").split("\t")
                rows.append(
                    (
                        listing_id,
                        buyer_id,
                        sort_time,
                        _parse_int_text(offer_type),
                        _parse_int_text(status),
                        _parse_float_text(price),
                        _parse_int_text(slr_hist),
                        _parse_int_text(byr_hist),
                    )
                )
        rows.sort(key=lambda item: (item[0], item[1], item[2], item[3] if item[3] is not None else -1, item[5] if item[5] is not None else -math.inf, item[4] if item[4] is not None else -1))
        _consume_grouped_thread_rows(conn, rows)
    shutil.rmtree(bucket_dir)
    conn.commit()


def _consume_grouped_thread_rows(conn: sqlite3.Connection, rows: list[tuple[str, str, str, int | None, int | None, float | None, int | None, int | None]]) -> None:
    listing_batch = []
    buyer_batch = []
    summary_batch = []
    current_key = None
    current_rows = []
    for row in rows:
        key = (row[0], row[1])
        if current_key is not None and key != current_key:
            _append_group_records(current_key, current_rows, listing_batch, buyer_batch, summary_batch)
            if len(summary_batch) >= 50_000:
                _flush_group_batches(conn, listing_batch, buyer_batch, summary_batch)
                listing_batch.clear()
                buyer_batch.clear()
                summary_batch.clear()
            current_rows = []
        current_key = key
        current_rows.append(row)
    if current_key is not None:
        _append_group_records(current_key, current_rows, listing_batch, buyer_batch, summary_batch)
    _flush_group_batches(conn, listing_batch, buyer_batch, summary_batch)


def _append_group_records(
    key: tuple[str, str],
    rows: list[tuple[str, str, str, int | None, int | None, float | None, int | None, int | None]],
    listing_batch: list[tuple[Any, ...]],
    buyer_batch: list[tuple[Any, ...]],
    summary_batch: list[tuple[Any, ...]],
) -> None:
    listing_id, buyer_id = key
    offer_types = [row[3] for row in rows]
    statuses = [row[4] for row in rows]
    prices = [row[5] for row in rows if row[5] is not None]
    times = [row[2] for row in rows]
    buyer_offer_count = sum(1 for offer_type in offer_types if offer_type in {0, 1})
    seller_offer_count = sum(1 for offer_type in offer_types if offer_type == 2)
    missing_counter = 0
    accept_not_last = 0
    for index, (offer_type, status) in enumerate(zip(offer_types, statuses, strict=True)):
        next_type = offer_types[index + 1] if index + 1 < len(offer_types) else None
        if status == 7 and offer_type in {0, 1} and next_type != 2:
            missing_counter = 1
        if status == 7 and offer_type == 2 and next_type != 1:
            missing_counter = 1
        if status in {1, 9} and index != len(offer_types) - 1:
            accept_not_last = 1
    listing_batch.append(
        (
            listing_id,
            1,
            max(prices) if prices else None,
            int(buyer_offer_count > 3),
            int(seller_offer_count > 3),
            missing_counter,
            accept_not_last,
            int(len(set(times)) != len(times)),
            1,
        )
    )
    if buyer_id:
        buyer_batch.append((buyer_id, buyer_offer_count, 1))
    first = rows[0]
    summary_batch.append(
        (
            listing_id,
            buyer_id,
            len(rows),
            int(any(status in {1, 9} for status in statuses)),
            first[5],
            first[4],
            first[3],
            first[6],
            first[7],
            ",".join("" if value is None else str(value) for value in offer_types[:6]),
            ",".join("" if value is None else str(value) for value in statuses[:6]),
        )
    )


def _flush_group_batches(
    conn: sqlite3.Connection,
    listing_batch: list[tuple[Any, ...]],
    buyer_batch: list[tuple[Any, ...]],
    summary_batch: list[tuple[Any, ...]],
) -> None:
    if listing_batch:
        conn.executemany(
            """
            INSERT INTO listing_thread_flags VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(listing_id) DO UPDATE SET
                has_thread = 1,
                max_offer = CASE
                    WHEN excluded.max_offer IS NULL THEN listing_thread_flags.max_offer
                    WHEN listing_thread_flags.max_offer IS NULL THEN excluded.max_offer
                    ELSE MAX(listing_thread_flags.max_offer, excluded.max_offer)
                END,
                crit_numoff_byr = MAX(listing_thread_flags.crit_numoff_byr, excluded.crit_numoff_byr),
                crit_numoff_slr = MAX(listing_thread_flags.crit_numoff_slr, excluded.crit_numoff_slr),
                crit_counter = MAX(listing_thread_flags.crit_counter, excluded.crit_counter),
                crit_accept = MAX(listing_thread_flags.crit_accept, excluded.crit_accept),
                crit_duplicate_time = MAX(listing_thread_flags.crit_duplicate_time, excluded.crit_duplicate_time),
                thread_group_count = listing_thread_flags.thread_group_count + excluded.thread_group_count
            """,
            listing_batch,
        )
    if buyer_batch:
        conn.executemany(
            """
            INSERT INTO buyer_offer_stats VALUES (?, ?, ?)
            ON CONFLICT(buyer_id) DO UPDATE SET
                num_offrs = buyer_offer_stats.num_offrs + excluded.num_offrs,
                num_threads = buyer_offer_stats.num_threads + excluded.num_threads
            """,
            buyer_batch,
        )
    if summary_batch:
        conn.executemany("INSERT INTO thread_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", summary_batch)
    conn.commit()


def _load_listing_rows(manifest: dict[str, Any], conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS listing_rows;
        CREATE TABLE listing_rows (
            listing_id TEXT PRIMARY KEY,
            seller_id TEXT NOT NULL,
            buyer_id TEXT,
            start_price REAL,
            item_price REAL,
            bo_ck INTEGER,
            condition_id INTEGER,
            bin_rev INTEGER,
            photo_count INTEGER,
            fdbk_pstv_start REAL,
            count4 INTEGER,
            ref_price4 REAL
        ) WITHOUT ROWID;
        """
    )
    lists_path = _source_path(manifest, "anon_bo_lists")
    batch = []
    with _open_text(lists_path) as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            batch.append(
                (
                    row.get("anon_item_id"),
                    row.get("anon_slr_id") or "",
                    row.get("anon_buyer_id") or None,
                    _float_or_none(row.get("start_price_usd")),
                    _float_or_none(row.get("item_price")),
                    _int_or_none(row.get("bo_ck_yn")),
                    _int_or_none(row.get("item_cndtn_id")),
                    _int_or_none(row.get("bin_rev")),
                    _int_or_none(row.get("photo_count")),
                    _float_or_none(row.get("fdbk_pstv_start")),
                    _int_or_none(row.get("count4")),
                    _float_or_none(row.get("ref_price4")),
                )
            )
            if len(batch) >= 100_000:
                conn.executemany("INSERT OR IGNORE INTO listing_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
                conn.commit()
                batch.clear()
    if batch:
        conn.executemany("INSERT OR IGNORE INTO listing_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
        conn.commit()


def _build_listing_sample_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS listing_sample;
        CREATE TABLE listing_sample AS
        SELECT
            l.listing_id,
            l.seller_id,
            l.buyer_id,
            l.start_price,
            l.item_price,
            l.bo_ck,
            l.condition_id,
            l.bin_rev,
            l.photo_count,
            l.fdbk_pstv_start,
            l.count4,
            l.ref_price4,
            COALESCE(f.has_thread, 0) AS has_thread,
            CASE WHEN l.start_price > 1000 THEN 1 ELSE 0 END AS crit_1k,
            CASE WHEN l.item_price IS NOT NULL AND l.start_price IS NOT NULL AND l.item_price > l.start_price THEN 1 ELSE 0 END AS crit_price,
            CASE WHEN f.max_offer IS NOT NULL AND l.start_price IS NOT NULL AND f.max_offer > l.start_price THEN 1 ELSE 0 END AS crit_offr,
            COALESCE(f.crit_numoff_byr, 0) AS crit_numoff_byr,
            COALESCE(f.crit_numoff_slr, 0) AS crit_numoff_slr,
            COALESCE(f.crit_counter, 0) AS crit_counter,
            COALESCE(f.crit_accept, 0) AS crit_accept,
            COALESCE(f.crit_duplicate_time, 0) AS crit_duplicate_time,
            CASE WHEN NOT (
                l.start_price > 1000
                OR (l.item_price IS NOT NULL AND l.start_price IS NOT NULL AND l.item_price > l.start_price)
                OR (f.max_offer IS NOT NULL AND l.start_price IS NOT NULL AND f.max_offer > l.start_price)
                OR COALESCE(f.crit_numoff_byr, 0) = 1
                OR COALESCE(f.crit_numoff_slr, 0) = 1
                OR COALESCE(f.crit_counter, 0) = 1
                OR COALESCE(f.crit_accept, 0) = 1
            ) THEN 1 ELSE 0 END AS sample_no_t5,
            CASE WHEN NOT (
                l.start_price > 1000
                OR (l.item_price IS NOT NULL AND l.start_price IS NOT NULL AND l.item_price > l.start_price)
                OR (f.max_offer IS NOT NULL AND l.start_price IS NOT NULL AND f.max_offer > l.start_price)
                OR COALESCE(f.crit_numoff_byr, 0) = 1
                OR COALESCE(f.crit_numoff_slr, 0) = 1
                OR COALESCE(f.crit_counter, 0) = 1
                OR COALESCE(f.crit_accept, 0) = 1
                OR COALESCE(f.crit_duplicate_time, 0) = 1
            ) THEN 1 ELSE 0 END AS sample_with_t5,
            CASE WHEN
                l.count4 IS NOT NULL
                AND l.count4 >= 20
                AND l.condition_id IS NOT NULL
                AND NOT (
                    l.start_price > 1000
                    OR (l.item_price IS NOT NULL AND l.start_price IS NOT NULL AND l.item_price > l.start_price)
                    OR (f.max_offer IS NOT NULL AND l.start_price IS NOT NULL AND f.max_offer > l.start_price)
                    OR COALESCE(f.crit_numoff_byr, 0) = 1
                    OR COALESCE(f.crit_numoff_slr, 0) = 1
                    OR COALESCE(f.crit_counter, 0) = 1
                    OR COALESCE(f.crit_accept, 0) = 1
                )
            THEN 1 ELSE 0 END AS ref_sample
        FROM listing_rows l
        LEFT JOIN listing_thread_flags f ON f.listing_id = l.listing_id;
        CREATE INDEX IF NOT EXISTS idx_listing_sample_listing ON listing_sample(listing_id);
        CREATE INDEX IF NOT EXISTS idx_listing_sample_seller ON listing_sample(seller_id) WHERE sample_no_t5 = 1;
        CREATE INDEX IF NOT EXISTS idx_listing_sample_buyer ON listing_sample(buyer_id) WHERE sample_no_t5 = 1 AND buyer_id IS NOT NULL;
        """
    )
    conn.commit()


def _calculate_observed_targets(conn: sqlite3.Connection) -> dict[str, Any]:
    raw_count = _scalar(conn, "SELECT COUNT(*) FROM listing_sample")
    sample_no_t5 = _scalar(conn, "SELECT SUM(sample_no_t5) FROM listing_sample")
    sample_with_t5 = _scalar(conn, "SELECT SUM(sample_with_t5) FROM listing_sample")
    l1 = _scalar(conn, "SELECT SUM(crit_1k) FROM listing_sample")
    l2 = _scalar(conn, "SELECT SUM(crit_price) FROM listing_sample")
    t1 = _scalar(conn, "SELECT SUM(crit_offr) FROM listing_sample")
    t2_buyer = _scalar(conn, "SELECT SUM(crit_numoff_byr) FROM listing_sample")
    t2_seller = _scalar(conn, "SELECT SUM(crit_numoff_slr) FROM listing_sample")
    t3 = _scalar(conn, "SELECT SUM(crit_counter) FROM listing_sample")
    t4 = _scalar(conn, "SELECT SUM(crit_accept) FROM listing_sample")
    t5 = _scalar(conn, "SELECT SUM(crit_duplicate_time) FROM listing_sample")
    listing_stats = _one_row(
        conn,
        """
        SELECT
            AVG(start_price) AS listing_price_mean,
            AVG(CASE WHEN condition_id IS NOT NULL THEN CASE WHEN condition_id >= 3000 THEN 1.0 ELSE 0.0 END END) AS used_rate,
            SUM(CASE WHEN condition_id IS NOT NULL THEN 1 ELSE 0 END) AS used_denominator,
            AVG(bin_rev) AS revised_rate,
            AVG(CASE WHEN item_price IS NOT NULL THEN 1.0 ELSE 0.0 END) AS sold_rate,
            AVG(CASE WHEN bo_ck = 1 THEN 1.0 ELSE 0.0 END) AS sold_best_offer_rate,
            AVG(CASE WHEN has_thread = 1 THEN 1.0 ELSE 0.0 END) AS received_offer_rate,
            AVG(CASE WHEN item_price IS NOT NULL AND start_price IS NOT NULL AND start_price != 0 THEN item_price / start_price END) AS sale_price_to_list,
            AVG(CASE WHEN bo_ck = 1 AND item_price IS NOT NULL AND start_price IS NOT NULL AND start_price != 0 THEN item_price / start_price END) AS bargained_price_to_list
        FROM listing_sample
        WHERE sample_no_t5 = 1
        """,
    )
    seller_stats = _one_row(
        conn,
        """
        SELECT COUNT(*) AS seller_count,
               SUM(CASE WHEN fdbk IS NOT NULL THEN 1 ELSE 0 END) AS feedback_nonmissing_sellers,
               AVG(fdbk) AS feedback_positive_percent_mean
        FROM (
            SELECT seller_id, MAX(fdbk_pstv_start) AS fdbk
            FROM listing_sample
            WHERE sample_no_t5 = 1
            GROUP BY seller_id
        )
        """,
    )
    buyer_count = _scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM (
            SELECT l.buyer_id
            FROM listing_sample l
            JOIN buyer_offer_stats b ON b.buyer_id = l.buyer_id
            WHERE l.sample_no_t5 = 1 AND l.buyer_id IS NOT NULL
            GROUP BY l.buyer_id
        )
        """,
    )
    thread_stats = _one_row(
        conn,
        """
        SELECT COUNT(*) AS thread_count,
               AVG(rounds) AS offer_count_mean,
               AVG(success) AS agreement_rate,
               AVG(first_offer_price / l.start_price) AS first_offer_to_list
        FROM thread_summaries t
        JOIN listing_sample l ON l.listing_id = t.listing_id
        WHERE l.sample_no_t5 = 1
          AND l.start_price IS NOT NULL
          AND l.start_price != 0
          AND t.first_offer_price IS NOT NULL
          AND (t.first_offer_price / l.start_price) <= 1
        """,
    )
    ref_listing = _one_row(
        conn,
        """
        SELECT COUNT(*) AS listing_count,
               AVG(CASE WHEN item_price IS NOT NULL THEN 1.0 ELSE 0.0 END) AS sold_rate,
               AVG(CASE WHEN has_thread = 1 THEN 1.0 ELSE 0.0 END) AS received_offer_rate,
               AVG(CASE WHEN bo_ck = 1 AND item_price IS NOT NULL AND start_price IS NOT NULL AND start_price != 0 THEN item_price / start_price END) AS bargained_price_to_list
        FROM listing_sample
        WHERE ref_sample = 1
        """,
    )
    ref_thread_agreement = _scalar(
        conn,
        """
        SELECT AVG(success)
        FROM thread_summaries t
        JOIN listing_sample l ON l.listing_id = t.listing_id
        WHERE l.ref_sample = 1
          AND l.start_price IS NOT NULL
          AND l.start_price != 0
          AND t.first_offer_price IS NOT NULL
          AND (t.first_offer_price / l.start_price) <= 1
        """,
    )
    root_game_tree = _scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM thread_summaries t
        JOIN listing_sample l ON l.listing_id = t.listing_id
        WHERE l.sample_no_t5 = 1 AND t.first_offer_type = 0
        """,
    )
    first_response = _one_row(
        conn,
        """
        SELECT
            AVG(CASE WHEN first_status IN (1, 9) THEN 1.0 ELSE 0.0 END) AS accept_share,
            AVG(CASE WHEN first_status = 7 THEN 1.0 ELSE 0.0 END) AS counter_share,
            AVG(CASE WHEN first_status NOT IN (1, 7, 9) OR first_status IS NULL THEN 1.0 ELSE 0.0 END) AS decline_share
        FROM thread_summaries t
        JOIN listing_sample l ON l.listing_id = t.listing_id
        WHERE l.sample_no_t5 = 1 AND t.first_offer_type = 0
        """,
    )
    observed = {
        "struct_raw_listings_before_restrictions": int(raw_count),
        "struct_main_sample_listings_after_restrictions": {
            "value": int(sample_no_t5),
            "released_code_with_t5": int(sample_with_t5),
            "crit_duplicate_time_count": int(t5),
        },
        "struct_l1_price_over_1000_exclusions": _count_fraction(l1, raw_count),
        "struct_l2_sale_price_above_listing_exclusions": _count_fraction(l2, raw_count),
        "struct_t1_offer_above_listing_exclusions": _count_fraction(t1, raw_count),
        "struct_t2_offer_limit_exclusions": {
            "buyer_count": int(t2_buyer),
            "buyer_fraction": _safe_div(t2_buyer, raw_count),
            "seller_count": int(t2_seller),
            "seller_fraction": _safe_div(t2_seller, raw_count),
        },
        "struct_t3_t4_sequence_integrity_exclusions": {
            "missing_counter_count": int(t3),
            "missing_counter_fraction": _safe_div(t3, raw_count),
            "accepted_not_last_count": int(t4),
            "accepted_not_last_fraction": _safe_div(t4, raw_count),
            "duplicate_timestamp_count_released_code_t5": int(t5),
        },
        "pub_table1_listing_price_mean": listing_stats["listing_price_mean"],
        "pub_table1_listing_used_rate": {
            "value": listing_stats["used_rate"],
            "nonmissing_denominator": int(listing_stats["used_denominator"]),
        },
        "pub_table1_listing_revised_rate": listing_stats["revised_rate"],
        "pub_table1_listing_sold_rate": listing_stats["sold_rate"],
        "pub_table1_listing_sold_best_offer_rate": listing_stats["sold_best_offer_rate"],
        "pub_table1_listing_received_offer_rate": listing_stats["received_offer_rate"],
        "pub_table1_listing_sale_price_to_list": listing_stats["sale_price_to_list"],
        "pub_table1_listing_bargained_price_to_list": listing_stats["bargained_price_to_list"],
        "pub_table1_seller_count_and_feedback_denominator": seller_stats,
        "pub_table1_buyer_count": int(buyer_count),
        "pub_table1_thread_count": int(thread_stats["thread_count"]),
        "pub_table1_thread_offer_count_mean": thread_stats["offer_count_mean"],
        "pub_table1_thread_agreement_rate": thread_stats["agreement_rate"],
        "pub_table1_thread_first_offer_to_list": thread_stats["first_offer_to_list"],
        "diag_ref_sample_listing_count": int(ref_listing["listing_count"]),
        "diag_ref_sample_sold_rate": ref_listing["sold_rate"],
        "diag_ref_sample_received_offer_rate": ref_listing["received_offer_rate"],
        "diag_ref_sample_bargained_price_to_list": ref_listing["bargained_price_to_list"],
        "diag_ref_sample_thread_agreement_rate": ref_thread_agreement,
        "diag_figure4_root_game_tree_count": int(root_game_tree),
        "diag_figure4_first_seller_response_shares": first_response,
    }
    return observed


def _replication_payload(
    root: Path,
    manifest: dict[str, Any],
    results: list[dict[str, Any]],
    *,
    targets_path: str | Path | None,
    scope: str,
) -> dict[str, Any]:
    target_path = Path(targets_path) if targets_path is not None else default_targets_path()
    fatal_failures = [item for item in results if item["fatal"] and item["passed"] is False]
    fatal_unevaluated = [item for item in results if item["fatal"] and item["passed"] is None]
    full_replication_passed = not fatal_failures and not fatal_unevaluated
    return {
        "schema_version": "nber_replication_check.v1",
        "normalized_dir": str(root.resolve()),
        "manifest_hash": sha256_file(root / "manifest.json"),
        "normalization_manifest_hash": manifest.get("lineage", {}).get("normalization_manifest_payload_hash") or manifest.get("lineage", {}).get("normalization_manifest_hash"),
        "normalization_manifest_payload_hash": manifest.get("lineage", {}).get("normalization_manifest_payload_hash"),
        "targets_hash": sha256_file(target_path),
        "replication_scope": scope,
        "results": results,
        "fatal_failures": fatal_failures,
        "fatal_unevaluated": fatal_unevaluated,
        "bounded_structure_passed": not fatal_failures,
        "full_replication_passed": full_replication_passed,
        "passed": full_replication_passed,
        "limitations": [
            "Published descriptive moments require the full official source and authors' sample restrictions.",
            "Sample-limited runs can validate structure and lineage but not published aggregate values.",
        ],
    }


def _iter_deduped_thread_rows(root: Path, manifest: dict[str, Any]) -> Iterator[dict[str, str]]:
    bucket_dir = root / "_tmp" / "thread_buckets"
    bucket_manifest = manifest.get("source_thread_pass", {}).get("bucket_manifest", {})
    if bucket_manifest.get("valid") is True and bucket_dir.exists():
        expected = {item["name"]: item for item in bucket_manifest.get("buckets", [])}
        for name in sorted(expected):
            path = bucket_dir / name
            if not path.exists() or sha256_file(path) != expected[name].get("sha256"):
                raise FileNotFoundError(f"Thread bucket missing or hash mismatch: {path}")
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        yield json.loads(line)
        return
    threads_path = _source_path(manifest, "anon_bo_threads")
    with _open_text(threads_path) as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            yield row


def _source_path(manifest: dict[str, Any], logical_name: str) -> Path:
    source = manifest.get("source_files", {}).get(logical_name, {})
    path_text = source.get("path")
    if path_text:
        return Path(path_text)
    raw_dir = Path(manifest.get("command_args", {}).get("raw_dir", "."))
    filename = "anon_bo_lists.csv" if logical_name == "anon_bo_lists" else "anon_bo_threads.csv"
    return _find_source(raw_dir, filename)


def _is_official_full_manifest(manifest: dict[str, Any]) -> bool:
    if manifest.get("command_args", {}).get("full") is not True:
        return False
    if manifest.get("command_args", {}).get("limit_threads") is not None:
        return False
    source_files = manifest.get("source_files", {})
    for logical_name, expected in OFFICIAL_FULL_SOURCE_EXPECTATIONS.items():
        source = source_files.get(logical_name, {})
        if source.get("sha256") != expected["sha256"] or source.get("bytes") != expected["bytes"]:
            return False
    return True


def _full_replication_signature(root: Path, manifest: dict[str, Any], target_path: Path) -> dict[str, Any]:
    return {
        "schema_version": "nber_full_replication_signature.v1",
        "replication_version": FULL_REPLICATION_VERSION,
        "normalized_dir": str(root.resolve()),
        "source_hashes": {name: manifest.get("source_files", {}).get(name, {}).get("sha256") for name in OFFICIAL_FULL_SOURCE_EXPECTATIONS},
        "source_bytes": {name: manifest.get("source_files", {}).get(name, {}).get("bytes") for name in OFFICIAL_FULL_SOURCE_EXPECTATIONS},
        "targets_hash": sha256_file(target_path),
        "mapping_hash": mapping_hash(),
        "normalization_manifest_payload_hash": manifest.get("lineage", {}).get("normalization_manifest_payload_hash") or manifest.get("lineage", {}).get("normalization_manifest_hash"),
        "thread_bucket_manifest_hash": _stable_hash(manifest.get("source_thread_pass", {}).get("bucket_manifest", {})),
    }


def _target_passed(target: dict[str, Any], observed: Any) -> bool:
    expected = target.get("expected", target.get("expected_value", target.get("formula")))
    tolerance = target.get("tolerance", {})
    if isinstance(observed, dict) and "value" in observed and isinstance(expected, dict) and "value" in expected:
        return _within(observed["value"], expected["value"], _absolute_tolerance(tolerance, "value")) and _dict_expected_matches(observed, expected, tolerance, skip={"type", "value"})
    if isinstance(observed, dict) and isinstance(expected, dict):
        expected_type = expected.get("type")
        if expected_type in {"integer", "float"} and "value" in expected:
            return _within(observed.get("value"), expected["value"], _absolute_tolerance(tolerance, "value"))
        return _dict_expected_matches(observed, expected, tolerance, skip={"type"})
    if isinstance(expected, dict) and "value" in expected:
        return _within(observed, expected["value"], _absolute_tolerance(tolerance, "value"))
    return observed == expected


def _dict_expected_matches(observed: dict[str, Any], expected: dict[str, Any], tolerance: dict[str, Any], *, skip: set[str]) -> bool:
    for key, expected_value in expected.items():
        if key in skip:
            continue
        if key not in observed:
            return False
        if not _within(observed[key], expected_value, _absolute_tolerance(tolerance, key)):
            return False
    return True


def _absolute_tolerance(tolerance: dict[str, Any], key: str) -> float:
    if key in {"count", "value"} and tolerance.get("type") == "exact":
        return float(tolerance.get("absolute", 0))
    if key.endswith("count") or key.endswith("denominator") or key in {"count", "seller_count", "buyer_count", "seller_count"}:
        return float(tolerance.get("count_absolute", tolerance.get("absolute", 0)))
    if key.endswith("fraction") or key.endswith("rate") or key.endswith("share"):
        return float(tolerance.get("fraction_absolute", tolerance.get("value_absolute", tolerance.get("absolute", 0))))
    if key.endswith("mean"):
        return float(tolerance.get("mean_absolute", tolerance.get("absolute", 0)))
    return float(tolerance.get("value_absolute", tolerance.get("absolute", tolerance.get("count_absolute", 0))))


def _within(observed: Any, expected: Any, tolerance: float) -> bool:
    if observed is None or expected is None:
        return observed is expected
    try:
        return abs(float(observed) - float(expected)) <= tolerance
    except (TypeError, ValueError):
        return observed == expected


def _flatten_targets(targets: dict[str, Any]) -> list[dict[str, Any]]:
    if "targets" in targets:
        return [dict(item) for item in targets["targets"]]
    rows = []
    for level, items in targets.get("levels", {}).items():
        for item in items:
            row = dict(item)
            row["level"] = level
            rows.append(row)
    return rows


def _result(target: dict[str, Any], *, passed: bool | None, observed: Any) -> dict[str, Any]:
    if passed is True:
        evaluation_status = "passed"
    elif passed is False:
        evaluation_status = "failed"
    else:
        evaluation_status = "not_evaluated"
    return {
        "id": target["id"],
        "level": target.get("level"),
        "fatal": _is_fatal(target),
        "passed": passed,
        "evaluation_status": evaluation_status,
        "observed": observed,
        "expected": target.get("expected", target.get("expected_value", target.get("formula"))),
        "tolerance": target.get("tolerance"),
    }


def _is_fatal(target: dict[str, Any]) -> bool:
    if "fatal" in target:
        return bool(target["fatal"])
    return str(target.get("status", "")).lower() == "fatal"


def _structural_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "struct_raw_listings_before_restrictions": manifest.get("source_inventory", {}).get("anon_bo_lists", {}).get("rows"),
        "struct_main_sample_listings_after_restrictions": "requires paper_sample restrictions on full source",
        "struct_l1_price_over_1000_exclusions": "requires full listing source",
        "struct_l2_sale_price_above_listing_exclusions": "requires full listing source",
        "struct_t1_offer_above_listing_exclusions": "requires full listing-thread join",
        "struct_t2_offer_limit_exclusions": "requires full thread grouping",
        "struct_t3_t4_sequence_integrity_exclusions": "requires full thread grouping",
    }


def _configure_sqlite(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=FILE")
    conn.execute("PRAGMA cache_size=-262144")


def _scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def _one_row(conn: sqlite3.Connection, sql: str) -> dict[str, Any]:
    cursor = conn.execute(sql)
    row = cursor.fetchone()
    return dict(zip([column[0] for column in cursor.description], row, strict=True))


def _count_fraction(count: int | float | None, denominator: int | float | None) -> dict[str, Any]:
    count_int = int(count or 0)
    return {"count": count_int, "fraction": _safe_div(count_int, denominator)}


def _safe_div(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if denominator in {0, None}:
        return None
    return float(numerator or 0) / float(denominator)


def _bucket_for(text: str, bucket_count: int) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16) % bucket_count


def _parse_int_text(text: str) -> int | None:
    if text == "":
        return None
    return int(text)


def _parse_float_text(text: str) -> float | None:
    if text == "":
        return None
    return float(text)


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest().upper()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
