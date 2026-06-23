from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any

from behavior_lab.datasets.nber_best_offer.full_listing_pass import FINAL_CONTRACT_VERSION, inspect_full_listing_restrictions
from behavior_lab.datasets.nber_best_offer.real_normalize import OFFICIAL_FULL_SOURCE_EXPECTATIONS, verify_full_release_evidence
from behavior_lab.datasets.nber_best_offer.restriction_forensics import FINAL_THREAD_TARGETS, inspect_thread_restriction_forensics
from behavior_lab.datasets.nber_best_offer.source_schema import sha256_file
from behavior_lab.datasets.nber_best_offer.table1_forensics import FINAL_TABLE1_TARGETS


FINAL_PUBLICATION_EVIDENCE_VERSION = "nber_final_publication_evidence.v2"
ELIGIBILITY_TABLE_VERSION = "nber_listing_eligibility.v2"


class FinalPublicationEvidenceError(ValueError):
    pass


def default_normalized_dir() -> Path:
    return Path(os.environ.get("OFFERLAB_DATA_ROOT", r"C:\OfferLabData")) / "processed" / "nber_best_offer_full"


def build_authoritative_eligibility_table(
    replication_db_or_normalized_dir: str | Path | None = None,
    *,
    output_db: str | Path | None = None,
    source_hash: str | None = None,
    hash_output_db: bool = False,
) -> dict[str, Any]:
    db_path, normalized_dir = _resolve_replication_db(replication_db_or_normalized_dir)
    if not db_path.exists():
        raise FinalPublicationEvidenceError(f"Missing replication database: {db_path}")
    out_path = Path(output_db) if output_db is not None else db_path.parent / "listing_eligibility_v2.sqlite"
    out_manifest_path = out_path.with_suffix(out_path.suffix + ".manifest.json")
    resolved_source_hash = source_hash or _source_hash_from_normalized_dir(normalized_dir) or _source_hash_from_db_file(db_path)
    signature = {
        "schema_version": "nber_listing_eligibility_signature.v1",
        "eligibility_table_version": ELIGIBILITY_TABLE_VERSION,
        "replication_db": str(db_path.resolve()),
        "replication_db_bytes": db_path.stat().st_size,
        "source_hash": resolved_source_hash,
        "restriction_contract_version": FINAL_CONTRACT_VERSION,
        "columns": _eligibility_columns(),
    }
    current = _load_json(out_manifest_path)
    if current and current.get("signature") == signature and _eligibility_db_valid(out_path, current):
        current["idempotent_rerun"] = True
        return current

    source_conn = sqlite3.connect(db_path)
    try:
        _require_listing_sample_columns(source_conn)
        source_count = int(source_conn.execute("SELECT COUNT(*) FROM listing_sample").fetchone()[0])
    finally:
        source_conn.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="eligibility-v2-", dir=out_path.parent))
    temp_db = temp_dir / out_path.name
    try:
        conn = sqlite3.connect(temp_db)
        try:
            conn.execute("PRAGMA journal_mode=OFF")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA temp_store=FILE")
            conn.execute("ATTACH DATABASE ? AS source_db", (str(db_path.resolve()),))
            conn.executescript(
                """
                CREATE TABLE listing_eligibility (
                    listing_id TEXT PRIMARY KEY,
                    L1_violation INTEGER NOT NULL,
                    L2_violation INTEGER NOT NULL,
                    T1_violation INTEGER NOT NULL,
                    T2_buyer_violation INTEGER NOT NULL,
                    T2_seller_violation INTEGER NOT NULL,
                    T3_violation INTEGER NOT NULL,
                    T4_violation INTEGER NOT NULL,
                    T5_violation INTEGER NOT NULL,
                    eligible_main_sample INTEGER NOT NULL,
                    source_hash TEXT NOT NULL,
                    restriction_contract_version TEXT NOT NULL
                ) WITHOUT ROWID;
                """
            )
            conn.execute(
                """
                INSERT INTO listing_eligibility
                SELECT
                    listing_id,
                    CAST(COALESCE(crit_1k, 0) AS INTEGER),
                    CAST(COALESCE(crit_price, 0) AS INTEGER),
                    CAST(COALESCE(crit_offr, 0) AS INTEGER),
                    CAST(COALESCE(crit_numoff_byr, 0) AS INTEGER),
                    CAST(COALESCE(crit_numoff_slr, 0) AS INTEGER),
                    CAST(COALESCE(crit_counter, 0) AS INTEGER),
                    CAST(COALESCE(crit_accept, 0) AS INTEGER),
                    CAST(COALESCE(crit_duplicate_time, 0) AS INTEGER),
                    CAST(COALESCE(sample_with_t5, 0) AS INTEGER),
                    ?,
                    ?
                FROM source_db.listing_sample
                """,
                (resolved_source_hash, FINAL_CONTRACT_VERSION),
            )
            inserted = int(conn.execute("SELECT COUNT(*) FROM listing_eligibility").fetchone()[0])
            if inserted != source_count:
                raise FinalPublicationEvidenceError(f"Eligibility row count mismatch: {inserted}!={source_count}")
            conn.commit()
            conn.execute("DETACH DATABASE source_db")
        finally:
            conn.close()
        if out_path.exists():
            out_path.unlink()
        shutil.move(str(temp_db), str(out_path))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    payload = {
        "schema_version": ELIGIBILITY_TABLE_VERSION,
        "status": "complete",
        "signature": signature,
        "replication_db": str(db_path.resolve()),
        "replication_db_bytes": db_path.stat().st_size,
        "output_db": str(out_path.resolve()),
        "output_db_bytes": out_path.stat().st_size,
        "output_db_sha256": sha256_file(out_path) if hash_output_db else None,
        "output_db_hash_policy": "sha256 omitted by default for large SQLite artifacts; row count and upstream source hashes still bind the table.",
        "table": {
            "name": "listing_eligibility",
            "rows": source_count,
            "columns": _eligibility_columns(),
            "primary_key": "listing_id",
        },
        "source_hash": resolved_source_hash,
        "restriction_contract_version": FINAL_CONTRACT_VERSION,
    }
    _write_atomic_json(out_manifest_path, payload)
    return json.loads(out_manifest_path.read_text(encoding="utf-8"))


def finalize_final_publication_evidence(
    normalized_dir: str | Path | None = None,
    *,
    output_dir: str | Path | None = None,
    contract_path: str | Path | None = None,
    build_eligibility: bool = True,
    hash_eligibility_db: bool = False,
) -> dict[str, Any]:
    root = Path(normalized_dir) if normalized_dir is not None else default_normalized_dir()
    out = Path(output_dir) if output_dir is not None else root
    out.mkdir(parents=True, exist_ok=True)
    manifest = _load_json(root / "manifest.json") or {}
    contract = _load_contract(contract_path)
    source_hashes = _source_hashes(manifest)
    normalization_hash = _normalization_payload_hash(manifest, root)

    eligibility = None
    eligibility_error = None
    if build_eligibility:
        try:
            eligibility = build_authoritative_eligibility_table(root, source_hash=_stable_hash(source_hashes), hash_output_db=hash_eligibility_db)
        except Exception as exc:  # pragma: no cover - exact message is reported in artifacts
            eligibility_error = f"{type(exc).__name__}: {exc}"

    table1_path = root / "table1_forensics_v2.json"
    thread_path = root / "thread_restriction_forensics" / "manifest.json"
    listing_path = root / "listing_restrictions" / "manifest.json"
    table1 = _load_json(table1_path) or {}
    thread = _load_json(thread_path) or {}
    listing = _load_json(listing_path) or {}

    fatal_results = _fatal_target_results(contract, table1, thread)
    replication_passed = all(result["passed"] for result in fatal_results)
    replication_check = {
        "schema_version": "nber_final_publication_replication_check.v2",
        "scope": "full_release",
        "normalized_dir": str(root.resolve()),
        "normalization_manifest_hash": normalization_hash,
        "source_hashes": source_hashes,
        "contract_version": contract.get("contract_version"),
        "target_results": fatal_results,
        "fatal_failures": [result for result in fatal_results if not result["passed"]],
        "fatal_unevaluated": [result for result in fatal_results if result.get("observed") == "not_evaluated"],
        "passed": replication_passed,
        "full_replication_passed": replication_passed,
        "artifacts": _artifact_refs(
            {
                "normalization_manifest": root / "manifest.json",
                "table1_forensics": table1_path,
                "thread_restriction_forensics": thread_path,
                "listing_restrictions": listing_path,
                "eligibility_manifest": Path(eligibility["output_db"]).with_suffix(Path(eligibility["output_db"]).suffix + ".manifest.json") if eligibility else None,
            }
        ),
    }
    replication_path = out / "replication_check_v2.json"
    _write_atomic_json(replication_path, replication_check)

    overlap_path = out / "restriction_overlap_matrix_v2.json"
    overlap = {
        "schema_version": "nber_restriction_overlap_matrix.v2",
        "scope": "full_release",
        "normalization_manifest_hash": normalization_hash,
        "source": str(table1_path.resolve()),
        "source_sha256": sha256_file(table1_path) if table1_path.exists() else None,
        "matrix": table1.get("restriction_overlap_matrix", []),
    }
    _write_atomic_json(overlap_path, overlap)

    version_report_path = out / "final_vs_working_paper_report.json"
    version_report = {
        "schema_version": "nber_final_vs_working_paper_report.v2",
        "scope": "full_release",
        "contract_version": contract.get("contract_version"),
        "version_difference_table": contract.get("version_difference_table", []),
        "working_paper_targets": contract.get("working_paper_targets", {}),
        "final_publication_targets": contract.get("final_publication_targets", {}),
        "stale_repository_values": contract.get("stale_repository_values", []),
    }
    _write_atomic_json(version_report_path, version_report)

    deterministic_checks = _deterministic_audit_checks(
        root=root,
        manifest=manifest,
        table1=table1,
        thread=thread,
        listing=listing,
        eligibility=eligibility,
        eligibility_error=eligibility_error,
        replication_check=replication_check,
        table1_path=table1_path,
        thread_path=thread_path,
        listing_path=listing_path,
    )
    independent_passed = all(check["passed"] for check in deterministic_checks)
    independent_audit = {
        "schema_version": "nber_final_publication_independent_audit.v2",
        "scope": "full_release",
        "normalization_manifest_hash": normalization_hash,
        "source_hashes": source_hashes,
        "checks": deterministic_checks,
        "failures": [check for check in deterministic_checks if not check["passed"]],
        "no_leakage_findings": _no_leakage_findings(manifest),
        "passed": independent_passed,
        "independent_audit_passed": independent_passed,
    }
    independent_path = out / "independent_audit_v2.json"
    _write_atomic_json(independent_path, independent_audit)

    gates = _gate_report(
        manifest=manifest,
        table1=table1,
        thread=thread,
        listing=listing,
        eligibility=eligibility,
        replication_check=replication_check,
        independent_audit=independent_audit,
    )
    finalize_report = {
        "schema_version": FINAL_PUBLICATION_EVIDENCE_VERSION,
        "scope": "full_release",
        "normalized_dir": str(root.resolve()),
        "normalization_manifest_hash": normalization_hash,
        "source_hashes": source_hashes,
        "contract_version": contract.get("contract_version"),
        "negotiation_benchmark_ready": gates["negotiation_benchmark_ready"],
        "paper_replication_complete": gates["paper_replication_complete"],
        "gates": gates,
        "artifacts": _artifact_refs(
            {
                "replication_check_v2": replication_path,
                "independent_audit_v2": independent_path,
                "restriction_overlap_matrix_v2": overlap_path,
                "final_vs_working_paper_report": version_report_path,
                "eligibility_manifest": Path(eligibility["output_db"]).with_suffix(Path(eligibility["output_db"]).suffix + ".manifest.json") if eligibility else None,
            }
        ),
        "replication_check": {
            "passed": replication_check["passed"],
            "fatal_failures": replication_check["fatal_failures"],
            "fatal_unevaluated": replication_check["fatal_unevaluated"],
        },
        "independent_audit": {
            "passed": independent_audit["passed"],
            "failures": independent_audit["failures"],
        },
        "model_training": "not_run",
    }
    final_path = out / "finalize_evidence_report_v2.json"
    _write_atomic_json(final_path, finalize_report)
    return json.loads(final_path.read_text(encoding="utf-8"))


def _resolve_replication_db(path: str | Path | None) -> tuple[Path, Path | None]:
    if path is None:
        normalized = default_normalized_dir()
        return normalized / "_replication" / "full_replication.sqlite", normalized
    candidate = Path(path)
    if candidate.is_dir():
        return candidate / "_replication" / "full_replication.sqlite", candidate
    return candidate, candidate.parent.parent if candidate.parent.name == "_replication" else None


def _eligibility_columns() -> list[str]:
    return [
        "listing_id",
        "L1_violation",
        "L2_violation",
        "T1_violation",
        "T2_buyer_violation",
        "T2_seller_violation",
        "T3_violation",
        "T4_violation",
        "T5_violation",
        "eligible_main_sample",
        "source_hash",
        "restriction_contract_version",
    ]


def _require_listing_sample_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(listing_sample)")}
    required = {
        "listing_id",
        "crit_1k",
        "crit_price",
        "crit_offr",
        "crit_numoff_byr",
        "crit_numoff_slr",
        "crit_counter",
        "crit_accept",
        "crit_duplicate_time",
        "sample_with_t5",
    }
    missing = sorted(required - columns)
    if missing:
        raise FinalPublicationEvidenceError(f"listing_sample missing columns: {missing}")


def _eligibility_db_valid(path: Path, manifest: dict[str, Any]) -> bool:
    if not path.exists():
        return False
    expected_columns = _eligibility_columns()
    expected_source_hash = manifest.get("source_hash")
    expected_contract = manifest.get("restriction_contract_version")
    try:
        conn = sqlite3.connect(path)
        try:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(listing_eligibility)")]
            if columns != expected_columns:
                return False
            table_count = int(conn.execute("SELECT COUNT(*) FROM listing_eligibility").fetchone()[0])
            if table_count != manifest.get("table", {}).get("rows"):
                return False
            mismatch = conn.execute(
                """
                SELECT 1
                FROM listing_eligibility
                WHERE source_hash != ? OR restriction_contract_version != ?
                LIMIT 1
                """,
                (expected_source_hash, expected_contract),
            ).fetchone()
            return mismatch is None
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _load_contract(contract_path: str | Path | None) -> dict[str, Any]:
    path = Path(contract_path) if contract_path is not None else Path(__file__).resolve().parents[4] / "datasets" / "manifests" / "nber_final_publication_contract.json"
    payload = _load_json(path)
    if not payload:
        raise FinalPublicationEvidenceError(f"Missing final publication contract: {path}")
    return payload


def _fatal_target_results(contract: dict[str, Any], table1: dict[str, Any], thread: dict[str, Any]) -> list[dict[str, Any]]:
    targets = contract.get("final_publication_targets", {})
    table1_observed = table1.get("observed_targets", {}) if isinstance(table1, dict) else {}
    thread_observed = thread.get("observed", {}) if isinstance(thread, dict) else {}
    result_specs = [
        ("source_listings", "source_listings", _raw_listing_count(table1)),
        ("final_listings", "final_listings", table1_observed.get("listings")),
        ("sellers", "sellers", table1_observed.get("sellers")),
        ("buyers", "buyers", table1_observed.get("buyers")),
        ("threads", "threads", table1_observed.get("threads")),
        ("T1_offer_above_listing_violations", "T1_offer_above_listing_violations", _restriction_count(table1, "T1")),
        ("T2_buyer_violations", "T2_buyer_violations", thread_observed.get("T2_buyer_violation_listing_count")),
        ("T2_seller_violations", "T2_seller_violations", thread_observed.get("T2_seller_violation_listing_count")),
        ("T3_missing_counter_violations", "T3_missing_counter_violations", thread_observed.get("T3_violation_listing_count")),
        ("T4_accepted_not_last_violations", "T4_accepted_not_last_violations", thread_observed.get("T4_violation_listing_count")),
        ("T5_duplicate_timestamp_violations", "T5_duplicate_timestamp_violations", thread_observed.get("T5_violation_listing_count")),
        ("missing_used_values", "missing_used_values", table1_observed.get("missing_used_listing_values")),
        ("sellers_with_missing_feedback", "sellers_with_missing_feedback", table1_observed.get("sellers_missing_feedback")),
    ]
    results = []
    for result_id, target_key, observed in result_specs:
        expected = targets.get(target_key)
        if observed is None:
            observed_value: Any = "not_evaluated"
            passed = False
        else:
            observed_value = observed
            passed = observed == expected
        results.append(
            {
                "id": result_id,
                "expected": expected,
                "observed": observed_value,
                "passed": passed,
                "fatal": True,
                "tolerance": {"type": "exact", "absolute": 0},
            }
        )
    return results


def _raw_listing_count(table1: dict[str, Any]) -> int | None:
    for row in table1.get("reconciliation_waterfall", []):
        if row.get("step") == "raw_source":
            return row.get("retained_listings")
    return None


def _restriction_count(table1: dict[str, Any], flag: str) -> int | None:
    total = 0
    found = False
    for row in table1.get("restriction_overlap_matrix", []):
        flags = row.get("flags", {})
        if flags.get(flag) is True:
            total += int(row.get("count", 0))
            found = True
    return total if found else None


def _deterministic_audit_checks(
    *,
    root: Path,
    manifest: dict[str, Any],
    table1: dict[str, Any],
    thread: dict[str, Any],
    listing: dict[str, Any],
    eligibility: dict[str, Any] | None,
    eligibility_error: str | None,
    replication_check: dict[str, Any],
    table1_path: Path,
    thread_path: Path,
    listing_path: Path,
) -> list[dict[str, Any]]:
    evidence_report = verify_full_release_evidence(manifest) if manifest else {"passed": False, "failures": ["missing_normalization_manifest"]}
    thread_inspection = _safe_thread_inspection(thread_path)
    listing_inspection = _safe_listing_inspection(listing_path)
    checks = [
        _check("normalization_manifest_present", bool(manifest), root / "manifest.json"),
        _check("normalization_full_unbounded", manifest.get("command_args", {}).get("full") is True and manifest.get("command_args", {}).get("limit_threads") is None),
        _check("official_sources_match", _official_sources_match(manifest)),
        _check("table1_forensics_present", bool(table1), table1_path),
        _check("table1_binds_to_current_replication_db", _table1_binds_to_current_replication_db(root, table1), details=_table1_binding_details(root, table1)),
        _check("table1_targets_pass", bool(table1.get("passed"))),
        _check("thread_forensics_present", bool(thread), thread_path),
        _check("thread_artifacts_valid", bool(thread_inspection.get("valid")), details=thread_inspection.get("failures", [])),
        _check("thread_final_targets_pass", bool(thread.get("final_target_comparison", {}).get("passed"))),
        _check("full_listing_pass_present", bool(listing), listing_path),
        _check("full_listing_pass_valid", bool(listing_inspection.get("valid")), details=listing_inspection.get("failures", [])),
        _check("eligibility_table_built", eligibility is not None, details=eligibility_error),
        _check("eligibility_row_count_matches_source", _eligibility_row_count_passes(eligibility, table1)),
        _check("replication_check_all_fatal_targets_pass", bool(replication_check.get("passed"))),
        _check("legacy_full_release_gate_not_forged", bool(evidence_report.get("passed")) is False or bool(manifest.get("audited_full_release_evidence", {}).get("passed")) is True, details=evidence_report.get("failures", [])),
    ]
    return checks


def _table1_binds_to_current_replication_db(root: Path, table1: dict[str, Any]) -> bool:
    details = _table1_binding_details(root, table1)
    return bool(details["path_matches"] and details["bytes_match"] and details["sha256_match"] and details["contract_matches"])


def _table1_binding_details(root: Path, table1: dict[str, Any]) -> dict[str, Any]:
    db_path = root / "_replication" / "full_replication.sqlite"
    recorded_path = table1.get("replication_db")
    recorded_sha = table1.get("replication_db_sha256")
    actual_exists = db_path.exists()
    actual_bytes = db_path.stat().st_size if actual_exists else None
    path_matches = False
    if recorded_path and actual_exists:
        try:
            path_matches = Path(recorded_path).resolve() == db_path.resolve()
        except OSError:
            path_matches = False
    sha_match = recorded_sha is None
    if recorded_sha is not None and actual_exists:
        sha_match = sha256_file(db_path) == recorded_sha
    return {
        "expected_replication_db": str(db_path.resolve()),
        "recorded_replication_db": recorded_path,
        "path_matches": path_matches,
        "expected_bytes": actual_bytes,
        "recorded_bytes": table1.get("replication_db_bytes"),
        "bytes_match": actual_bytes is not None and table1.get("replication_db_bytes") == actual_bytes,
        "recorded_sha256": recorded_sha,
        "sha256_match": sha_match,
        "restriction_contract_version": table1.get("restriction_contract_version"),
        "contract_matches": table1.get("restriction_contract_version") == FINAL_CONTRACT_VERSION,
    }


def _gate_report(
    *,
    manifest: dict[str, Any],
    table1: dict[str, Any],
    thread: dict[str, Any],
    listing: dict[str, Any],
    eligibility: dict[str, Any] | None,
    replication_check: dict[str, Any],
    independent_audit: dict[str, Any],
) -> dict[str, Any]:
    checks = {
        "complete_thread_normalization": manifest.get("command_args", {}).get("full") is True
        and manifest.get("command_args", {}).get("limit_threads") is None
        and _turn_rows_match_thread_forensics(manifest, thread),
        "exact_final_t1_t5_semantics": _target_subset_passes(
            replication_check,
            {
                "T1_offer_above_listing_violations",
                "T2_buyer_violations",
                "T2_seller_violations",
                "T3_missing_counter_violations",
                "T4_accepted_not_last_violations",
                "T5_duplicate_timestamp_violations",
            },
        ),
        "listing_level_propagation": thread.get("semantics", {}).get("listing_level_propagation") is True,
        "exact_retained_thread_count": _target_subset_passes(replication_check, {"threads"}),
        "exact_retained_buyer_count": _target_subset_passes(replication_check, {"buyers"}),
        "complete_listing_joins_for_retained_threads": _target_subset_passes(replication_check, {"source_listings", "final_listings"})
        and _eligibility_row_count_passes(eligibility, table1),
        "independent_audit_pass": independent_audit.get("independent_audit_passed") is True,
        "no_leakage_findings": independent_audit.get("no_leakage_findings") is True,
    }
    negotiation_failures = [name for name, passed in checks.items() if not passed]
    paper_checks = {
        **checks,
        "complete_98m_listing_pass": bool(listing),
        "exact_retained_listing_count": _target_subset_passes(replication_check, {"final_listings"}),
        "exact_seller_count": _target_subset_passes(replication_check, {"sellers"}),
        "exact_used_denominator": _target_subset_passes(replication_check, {"missing_used_values"}),
        "exact_feedback_denominator": _target_subset_passes(replication_check, {"sellers_with_missing_feedback"}),
        "all_fatal_table1_targets_pass": bool(table1.get("passed")),
    }
    paper_failures = [name for name, passed in paper_checks.items() if not passed]
    return {
        "negotiation_benchmark_ready": not negotiation_failures,
        "paper_replication_complete": not paper_failures,
        "negotiation_checks": checks,
        "negotiation_failures": negotiation_failures,
        "paper_checks": paper_checks,
        "paper_failures": paper_failures,
    }


def _safe_thread_inspection(thread_path: Path) -> dict[str, Any]:
    if not thread_path.exists():
        return {"valid": False, "failures": ["missing_thread_manifest"]}
    try:
        return inspect_thread_restriction_forensics(thread_path.parent)
    except Exception as exc:  # pragma: no cover - defensive path for malformed external artifacts
        return {"valid": False, "failures": [f"{type(exc).__name__}: {exc}"]}


def _safe_listing_inspection(listing_path: Path) -> dict[str, Any]:
    if not listing_path.exists():
        return {"valid": False, "failures": ["missing_listing_manifest"]}
    try:
        return inspect_full_listing_restrictions(listing_path.parent)
    except Exception as exc:  # pragma: no cover - defensive path for malformed external artifacts
        return {"valid": False, "failures": [f"{type(exc).__name__}: {exc}"]}


def _target_subset_passes(replication_check: dict[str, Any], ids: set[str]) -> bool:
    by_id = {row["id"]: row for row in replication_check.get("target_results", [])}
    return all(by_id.get(item, {}).get("passed") is True for item in ids)


def _turn_rows_match_thread_forensics(manifest: dict[str, Any], thread: dict[str, Any]) -> bool:
    rows = manifest.get("tables", {}).get("negotiation_turns", {}).get("rows")
    accepted = thread.get("bucket_manifest", {}).get("accepted_rows")
    return rows is not None and accepted is not None and int(rows) == int(accepted)


def _eligibility_row_count_passes(eligibility: dict[str, Any] | None, table1: dict[str, Any]) -> bool:
    if not eligibility:
        return False
    expected = _raw_listing_count(table1)
    observed = eligibility.get("table", {}).get("rows")
    return expected is not None and observed == expected


def _official_sources_match(manifest: dict[str, Any]) -> bool:
    source_files = manifest.get("source_files", {})
    for name, expected in OFFICIAL_FULL_SOURCE_EXPECTATIONS.items():
        actual = source_files.get(name, {})
        if actual.get("sha256") != expected["sha256"] or actual.get("bytes") != expected["bytes"]:
            return False
    return True


def _no_leakage_findings(manifest: dict[str, Any]) -> bool:
    text = json.dumps(manifest.get("audit_findings", []), sort_keys=True).lower()
    return "leak" not in text


def _source_hash_from_normalized_dir(normalized_dir: Path | None) -> str | None:
    if normalized_dir is None:
        return None
    manifest = _load_json(normalized_dir / "manifest.json") or {}
    source_hashes = _source_hashes(manifest)
    if source_hashes:
        return _stable_hash(source_hashes)
    return None


def _source_hash_from_db_file(db_path: Path) -> str:
    return hashlib.sha256(str(db_path.resolve()).encode("utf-8")).hexdigest().upper()


def _source_hashes(manifest: dict[str, Any]) -> dict[str, str]:
    lineage = manifest.get("lineage", {}) if isinstance(manifest, dict) else {}
    if isinstance(lineage, dict) and isinstance(lineage.get("raw_source_hashes"), dict):
        return {str(key): str(value) for key, value in lineage["raw_source_hashes"].items()}
    source_files = manifest.get("source_files", {}) if isinstance(manifest, dict) else {}
    return {str(key): str(value.get("sha256")) for key, value in source_files.items() if isinstance(value, dict) and value.get("sha256")}


def _normalization_payload_hash(manifest: dict[str, Any], root: Path) -> str:
    lineage = manifest.get("lineage", {}) if isinstance(manifest, dict) else {}
    if isinstance(lineage, dict):
        if lineage.get("normalization_manifest_payload_hash"):
            return str(lineage["normalization_manifest_payload_hash"])
        if lineage.get("normalization_manifest_hash"):
            return str(lineage["normalization_manifest_hash"])
    manifest_path = root / "manifest.json"
    return sha256_file(manifest_path) if manifest_path.exists() else _stable_hash({"missing_manifest": str(manifest_path.resolve())})


def _artifact_refs(paths: dict[str, Path | None]) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    for name, path in paths.items():
        if path is None:
            refs[name] = {"path": None, "exists": False, "sha256": None}
            continue
        refs[name] = {
            "path": str(path.resolve()),
            "exists": path.exists(),
            "sha256": sha256_file(path) if path.exists() else None,
        }
    return refs


def _check(name: str, passed: bool, path: Path | None = None, details: Any = None) -> dict[str, Any]:
    payload = {"name": name, "passed": bool(passed)}
    if path is not None:
        payload["path"] = str(path.resolve())
    if details is not None and details != []:
        payload["details"] = details
    return payload


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest().upper()


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, newline="\n") as handle:
        temp = Path(handle.name)
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp, path)
