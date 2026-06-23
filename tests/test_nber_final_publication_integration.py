from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import _bootstrap  # noqa: F401

from behavior_lab.datasets.nber_best_offer.final_publication import (
    build_authoritative_eligibility_table,
    finalize_final_publication_evidence,
)
from behavior_lab.datasets.nber_best_offer.real_normalize import OFFICIAL_FULL_SOURCE_EXPECTATIONS


class NberFinalPublicationIntegrationTests(unittest.TestCase):
    def test_builds_authoritative_listing_eligibility_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "_replication" / "full_replication.sqlite"
            db.parent.mkdir()
            self._write_replication_db(db)

            manifest = build_authoritative_eligibility_table(db, source_hash="SOURCE")

            self.assertEqual(manifest["table"]["rows"], 2)
            self.assertEqual(
                manifest["table"]["columns"],
                [
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
                ],
            )
            conn = sqlite3.connect(manifest["output_db"])
            try:
                row = conn.execute(
                    """
                    SELECT listing_id, L1_violation, T2_buyer_violation, eligible_main_sample, source_hash, restriction_contract_version
                    FROM listing_eligibility
                    WHERE listing_id = 'l2'
                    """
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row, ("l2", 1, 1, 0, "SOURCE", "2020_qje_final_released_code_v1"))

    def test_final_publication_audit_keeps_gates_closed_on_exact_target_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_normalized_fixture(root)

            report = finalize_final_publication_evidence(root)

            self.assertFalse(report["negotiation_benchmark_ready"])
            self.assertFalse(report["paper_replication_complete"])
            failed_ids = {row["id"] for row in report["replication_check"]["fatal_failures"]}
            self.assertIn("T3_missing_counter_violations", failed_ids)
            self.assertIn("T4_accepted_not_last_violations", failed_ids)
            self.assertEqual(report["model_training"], "not_run")

            for artifact in [
                "replication_check_v2.json",
                "independent_audit_v2.json",
                "finalize_evidence_report_v2.json",
                "restriction_overlap_matrix_v2.json",
                "final_vs_working_paper_report.json",
            ]:
                self.assertTrue((root / artifact).exists(), artifact)

            audit = json.loads((root / "independent_audit_v2.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["scope"], "full_release")
            self.assertFalse(audit["independent_audit_passed"])
            checks = {check["name"]: check for check in audit["checks"]}
            self.assertTrue(checks["table1_binds_to_current_replication_db"]["passed"])

    def test_stale_table1_artifact_blocks_independent_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_normalized_fixture(root)
            table1_path = root / "table1_forensics_v2.json"
            table1 = json.loads(table1_path.read_text(encoding="utf-8"))
            table1["replication_db"] = str(root / "_replication" / "stale.sqlite")
            table1_path.write_text(json.dumps(table1), encoding="utf-8")

            report = finalize_final_publication_evidence(root)

            self.assertFalse(report["negotiation_benchmark_ready"])
            audit = json.loads((root / "independent_audit_v2.json").read_text(encoding="utf-8"))
            checks = {check["name"]: check for check in audit["checks"]}
            self.assertFalse(checks["table1_binds_to_current_replication_db"]["passed"])

    def test_eligibility_rerun_rejects_wrong_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "_replication" / "full_replication.sqlite"
            self._write_replication_db(db)
            first = build_authoritative_eligibility_table(db, source_hash="SOURCE")
            conn = sqlite3.connect(first["output_db"])
            try:
                conn.execute("UPDATE listing_eligibility SET source_hash = 'STALE' WHERE listing_id = 'l1'")
                conn.commit()
            finally:
                conn.close()

            second = build_authoritative_eligibility_table(db, source_hash="SOURCE")

            self.assertNotIn("idempotent_rerun", second)
            conn = sqlite3.connect(second["output_db"])
            try:
                hashes = {row[0] for row in conn.execute("SELECT DISTINCT source_hash FROM listing_eligibility")}
            finally:
                conn.close()
            self.assertEqual(hashes, {"SOURCE"})

    @staticmethod
    def _write_replication_db(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        try:
            conn.executescript(
                """
                CREATE TABLE listing_sample (
                    listing_id TEXT,
                    crit_1k INTEGER,
                    crit_price INTEGER,
                    crit_offr INTEGER,
                    crit_numoff_byr INTEGER,
                    crit_numoff_slr INTEGER,
                    crit_counter INTEGER,
                    crit_accept INTEGER,
                    crit_duplicate_time INTEGER,
                    sample_with_t5 INTEGER
                );
                """
            )
            conn.executemany(
                "INSERT INTO listing_sample VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("l1", 0, 0, 0, 0, 0, 0, 0, 0, 1),
                    ("l2", 1, 0, 0, 1, 0, 1, 0, 1, 0),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def _write_normalized_fixture(self, root: Path) -> None:
        self._write_replication_db(root / "_replication" / "full_replication.sqlite")
        source_files = {
            name: {"path": str(root / f"{name}.csv.gz"), "sha256": expected["sha256"], "bytes": expected["bytes"]}
            for name, expected in OFFICIAL_FULL_SOURCE_EXPECTATIONS.items()
        }
        manifest = {
            "schema_version": "nber_real_normalized_manifest.v1",
            "status": "complete",
            "command_args": {"full": True, "limit_threads": None},
            "source_files": source_files,
            "tables": {"negotiation_turns": {"rows": 47375804}},
            "lineage": {
                "normalization_manifest_payload_hash": "payload-hash",
                "raw_source_hashes": {name: expected["sha256"] for name, expected in OFFICIAL_FULL_SOURCE_EXPECTATIONS.items()},
            },
            "audited_full_release_evidence": {"passed": False},
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        table1 = {
            "replication_db": str((root / "_replication" / "full_replication.sqlite").resolve()),
            "replication_db_bytes": (root / "_replication" / "full_replication.sqlite").stat().st_size,
            "replication_db_sha256": None,
            "restriction_contract_version": "2020_qje_final_released_code_v1",
            "passed": True,
            "observed_targets": {
                "listings": 88386471,
                "sellers": 1197397,
                "buyers": 4701301,
                "threads": 25453072,
                "missing_used_listing_values": 27678157,
                "sellers_missing_feedback": 51992,
            },
            "reconciliation_waterfall": [{"step": "raw_source", "retained_listings": 98307281}],
            "restriction_overlap_matrix": [{"flags": {"T1": True}, "count": 386096}],
        }
        (root / "table1_forensics_v2.json").write_text(json.dumps(table1), encoding="utf-8")
        thread_dir = root / "thread_restriction_forensics"
        thread_dir.mkdir()
        thread = {
            "schema_version": "nber_thread_restriction_forensics.v1",
            "semantics": {"listing_level_propagation": True},
            "bucket_manifest": {"accepted_rows": 47375804},
            "observed": {
                "T2_buyer_violation_listing_count": 3518,
                "T2_seller_violation_listing_count": 0,
                "T3_violation_listing_count": 1453,
                "T4_violation_listing_count": 1111,
                "T5_violation_listing_count": 4273,
            },
            "final_target_comparison": {
                "passed": False,
                "targets": [
                    {"target": "T2_buyer_violation_listing_count", "expected": 3518, "observed": 3518, "passed": True},
                    {"target": "T2_seller_violation_listing_count", "expected": 0, "observed": 0, "passed": True},
                    {"target": "T3_violation_listing_count", "expected": 1451, "observed": 1453, "passed": False},
                    {"target": "T4_violation_listing_count", "expected": 1109, "observed": 1111, "passed": False},
                    {"target": "T5_violation_listing_count", "expected": 4273, "observed": 4273, "passed": True},
                ],
            },
        }
        (thread_dir / "manifest.json").write_text(json.dumps(thread), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
