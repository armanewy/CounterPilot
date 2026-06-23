from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

from behavior_lab.datasets.nber_best_offer.full_listing_pass import FINAL_CONTRACT_VERSION
from behavior_lab.datasets.nber_best_offer.source_schema import sha256_file


TABLE1_FORENSICS_VERSION = "nber_table1_forensics.v1"
FINAL_TABLE1_TARGETS = {
    "listings": 88386471,
    "sellers": 1197397,
    "buyers": 4701301,
    "threads": 25453072,
    "missing_used_listing_values": 27678157,
    "sellers_missing_feedback": 51992,
}


class Table1ForensicsError(ValueError):
    pass


def default_replication_db() -> Path:
    return Path(os.environ.get("OFFERLAB_DATA_ROOT", r"C:\OfferLabData")) / "processed" / "nber_best_offer_full" / "_replication" / "full_replication.sqlite"


def audit_table1_denominators(
    replication_db_or_normalized_dir: str | Path | None = None,
    *,
    expected_targets: dict[str, int] | None = None,
    output_path: str | Path | None = None,
    hash_database: bool = False,
) -> dict[str, Any]:
    db_path = _resolve_db_path(replication_db_or_normalized_dir)
    if not db_path.exists():
        raise Table1ForensicsError(f"Missing replication database: {db_path}")
    targets = dict(expected_targets or FINAL_TABLE1_TARGETS)
    conn = sqlite3.connect(db_path)
    try:
        _require_tables(conn)
        listing = _listing_level(conn)
        seller = _seller_level(conn)
        buyer = _buyer_level(conn)
        thread = _thread_level(conn)
        waterfall = _waterfall(conn)
        overlap = _overlap_matrix(conn)
    finally:
        conn.close()
    observed = {
        "listings": listing["retained_listing_count"],
        "sellers": seller["seller_count"],
        "buyers": buyer["buyer_count"],
        "threads": thread["thread_count"],
        "missing_used_listing_values": listing["used_missing_count"],
        "sellers_missing_feedback": seller["feedback_missing_seller_count"],
    }
    target_results = [
        {
            "target": key,
            "expected": value,
            "observed": observed.get(key),
            "passed": observed.get(key) == value,
        }
        for key, value in targets.items()
    ]
    payload = {
        "schema_version": TABLE1_FORENSICS_VERSION,
        "restriction_contract_version": FINAL_CONTRACT_VERSION,
        "replication_db": str(db_path.resolve()),
        "replication_db_sha256": sha256_file(db_path) if hash_database else None,
        "replication_db_bytes": db_path.stat().st_size,
        "listing_level": listing,
        "seller_level": seller,
        "buyer_level": buyer,
        "thread_level": thread,
        "observed_targets": observed,
        "target_results": target_results,
        "passed": all(row["passed"] for row in target_results),
        "reconciliation_waterfall": waterfall,
        "restriction_overlap_matrix": overlap,
        "notes": [
            "All final counts use sample_with_t5, not the legacy sample_no_t5 population.",
            "Missing Used and missing feedback remain missing and are excluded only from their own nonmissing denominators.",
            "The final sample is computed by the union of listing-level flags, not by subtracting individual violations.",
        ],
    }
    if output_path is not None:
        _write_atomic_json(Path(output_path), payload)
    return payload


def _resolve_db_path(path: str | Path | None) -> Path:
    if path is None:
        return default_replication_db()
    candidate = Path(path)
    if candidate.is_dir():
        return candidate / "_replication" / "full_replication.sqlite"
    return candidate


def _require_tables(conn: sqlite3.Connection) -> None:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {"listing_sample", "thread_summaries", "buyer_offer_stats"}
    missing = sorted(required - tables)
    if missing:
        raise Table1ForensicsError(f"Replication database missing tables: {missing}")


def _listing_level(conn: sqlite3.Connection) -> dict[str, Any]:
    row = _one_row(
        conn,
        """
        SELECT
            COUNT(*) AS retained_listing_count,
            SUM(CASE WHEN condition_id >= 3000 THEN 1 ELSE 0 END) AS used_true_numerator,
            SUM(CASE WHEN condition_id IS NOT NULL THEN 1 ELSE 0 END) AS used_nonmissing_denominator,
            SUM(CASE WHEN condition_id IS NULL THEN 1 ELSE 0 END) AS used_missing_count,
            AVG(CASE WHEN condition_id IS NOT NULL THEN CASE WHEN condition_id >= 3000 THEN 1.0 ELSE 0.0 END END) AS used_rate
        FROM listing_sample
        WHERE sample_with_t5 = 1
        """,
    )
    return {
        "retained_listing_count": int(row["retained_listing_count"] or 0),
        "used_true_numerator": int(row["used_true_numerator"] or 0),
        "used_nonmissing_denominator": int(row["used_nonmissing_denominator"] or 0),
        "used_missing_count": int(row["used_missing_count"] or 0),
        "used_rate": row["used_rate"],
    }


def _seller_level(conn: sqlite3.Connection) -> dict[str, Any]:
    row = _one_row(
        conn,
        """
        WITH seller_panel AS (
            SELECT seller_id,
                   COUNT(*) AS listing_count,
                   MAX(fdbk_pstv_start) AS feedback
            FROM listing_sample
            WHERE sample_with_t5 = 1
            GROUP BY seller_id
        )
        SELECT
            COUNT(*) AS seller_count,
            SUM(CASE WHEN feedback IS NOT NULL THEN 1 ELSE 0 END) AS feedback_nonmissing_seller_count,
            SUM(CASE WHEN feedback IS NULL THEN 1 ELSE 0 END) AS feedback_missing_seller_count,
            AVG(feedback) AS feedback_positive_percent_mean,
            AVG(listing_count) AS listings_per_seller
        FROM seller_panel
        """,
    )
    return {
        "seller_count": int(row["seller_count"] or 0),
        "feedback_nonmissing_seller_count": int(row["feedback_nonmissing_seller_count"] or 0),
        "feedback_missing_seller_count": int(row["feedback_missing_seller_count"] or 0),
        "feedback_positive_percent_mean": row["feedback_positive_percent_mean"],
        "listings_per_seller": row["listings_per_seller"],
    }


def _buyer_level(conn: sqlite3.Connection) -> dict[str, Any]:
    row = _one_row(
        conn,
        """
        SELECT
            COUNT(DISTINCT CASE WHEN l.buyer_id IS NOT NULL AND l.buyer_id != '' THEN l.buyer_id END) AS buyer_count,
            SUM(CASE WHEN l.buyer_id IS NULL THEN 1 ELSE 0 END) AS null_buyer_listing_rows,
            SUM(CASE WHEN l.buyer_id = '' THEN 1 ELSE 0 END) AS empty_buyer_listing_rows,
            SUM(CASE WHEN LOWER(l.buyer_id) IN ('null', 'none', 'nan', '.') THEN 1 ELSE 0 END) AS sentinel_buyer_listing_rows
        FROM listing_sample l
        JOIN buyer_offer_stats b ON b.buyer_id = l.buyer_id
        WHERE l.sample_with_t5 = 1
        """,
    )
    thread_row = _one_row(
        conn,
        """
        SELECT COUNT(DISTINCT CASE WHEN t.buyer_id IS NOT NULL AND t.buyer_id != '' THEN t.buyer_id END) AS retained_thread_offer_buyer_count
        FROM thread_summaries t
        JOIN listing_sample l ON l.listing_id = t.listing_id
        WHERE l.sample_with_t5 = 1
        """,
    )
    return {
        "buyer_count": int(row["buyer_count"] or 0),
        "retained_thread_offer_buyer_count": int(thread_row["retained_thread_offer_buyer_count"] or 0),
        "null_buyer_listing_rows": int(row["null_buyer_listing_rows"] or 0),
        "empty_buyer_listing_rows": int(row["empty_buyer_listing_rows"] or 0),
        "sentinel_buyer_listing_rows": int(row["sentinel_buyer_listing_rows"] or 0),
        "released_code_denominator": "summary_stats_main.do builds buyer-level data by collapsing thread activity by anon_buyer_id, merging to anon_bo_lists by anon_buyer_id, imposing sample_id_list by listing, then collapsing by anon_buyer_id.",
        "thread_offer_buyer_note": "Distinct retained thread offer buyers are reported separately and are not the final Table I buyer denominator.",
    }


def _thread_level(conn: sqlite3.Connection) -> dict[str, Any]:
    row = _one_row(
        conn,
        """
        SELECT
            COUNT(*) AS thread_count,
            COUNT(DISTINCT t.listing_id || char(0) || t.buyer_id) AS distinct_listing_buyer_pairs,
            COUNT(*) - COUNT(DISTINCT t.listing_id || char(0) || t.buyer_id) AS duplicate_listing_buyer_pairs,
            SUM(CASE WHEN t.listing_id IS NULL OR t.listing_id = '' OR t.buyer_id IS NULL OR t.buyer_id = '' THEN 1 ELSE 0 END) AS malformed_identifier_threads
        FROM thread_summaries t
        JOIN listing_sample l ON l.listing_id = t.listing_id
        WHERE l.sample_with_t5 = 1
        """,
    )
    return {
        "thread_count": int(row["thread_count"] or 0),
        "distinct_listing_buyer_pairs": int(row["distinct_listing_buyer_pairs"] or 0),
        "duplicate_listing_buyer_pairs": int(row["duplicate_listing_buyer_pairs"] or 0),
        "malformed_identifier_threads": int(row["malformed_identifier_threads"] or 0),
        "source_thread_identifier_comparison": "thread_summaries is keyed by listing_id + buyer_id; source thread identifiers are not required for final Table I thread count.",
    }


def _waterfall(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    steps = [
        ("raw_source", "1=1"),
        ("after_L1", "crit_1k = 0"),
        ("after_L2", "crit_1k = 0 AND crit_price = 0"),
        ("after_T1", "crit_1k = 0 AND crit_price = 0 AND crit_offr = 0"),
        ("after_T2_buyer", "crit_1k = 0 AND crit_price = 0 AND crit_offr = 0 AND crit_numoff_byr = 0"),
        ("after_T2_seller", "crit_1k = 0 AND crit_price = 0 AND crit_offr = 0 AND crit_numoff_byr = 0 AND crit_numoff_slr = 0"),
        ("after_T3", "crit_1k = 0 AND crit_price = 0 AND crit_offr = 0 AND crit_numoff_byr = 0 AND crit_numoff_slr = 0 AND crit_counter = 0"),
        ("after_T4", "crit_1k = 0 AND crit_price = 0 AND crit_offr = 0 AND crit_numoff_byr = 0 AND crit_numoff_slr = 0 AND crit_counter = 0 AND crit_accept = 0"),
        ("after_T5", "sample_with_t5 = 1"),
    ]
    rows = []
    previous = None
    for name, predicate in steps:
        count = int(conn.execute(f"SELECT COUNT(*) FROM listing_sample WHERE {predicate}").fetchone()[0])
        rows.append({"step": name, "retained_listings": count, "removed_since_previous": None if previous is None else previous - count})
        previous = count
    raw = rows[0]["retained_listings"]
    final = rows[-1]["retained_listings"]
    rows.append({"step": "union_excluded", "retained_listings": None, "removed_since_previous": raw - final})
    rows.append({"step": "final_retained_sample", "retained_listings": final, "removed_since_previous": 0})
    return rows


def _overlap_matrix(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cursor = conn.execute(
        """
        SELECT
            crit_1k,
            crit_price,
            crit_offr,
            crit_numoff_byr,
            crit_numoff_slr,
            crit_counter,
            crit_accept,
            crit_duplicate_time,
            COUNT(*) AS count
        FROM listing_sample
        GROUP BY crit_1k, crit_price, crit_offr, crit_numoff_byr, crit_numoff_slr, crit_counter, crit_accept, crit_duplicate_time
        ORDER BY count DESC
        """
    )
    flags = ["L1", "L2", "T1", "T2_buyer", "T2_seller", "T3", "T4", "T5"]
    rows = []
    for db_row in cursor.fetchall():
        values = [int(value or 0) for value in db_row[:8]]
        rows.append({"flags": {flag: bool(value) for flag, value in zip(flags, values, strict=True)}, "count": int(db_row[8])})
    return rows


def _one_row(conn: sqlite3.Connection, sql: str) -> dict[str, Any]:
    cursor = conn.execute(sql)
    row = cursor.fetchone()
    if row is None:
        return {}
    return dict(zip([column[0] for column in cursor.description], row, strict=True))


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, newline="\n") as handle:
        temp = Path(handle.name)
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp, path)
