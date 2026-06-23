from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

import _bootstrap  # noqa: F401

from behavior_lab.datasets.nber_best_offer.table1_forensics import audit_table1_denominators


class NberTable1ForensicsTests(unittest.TestCase):
    def test_audits_final_sample_denominators_with_missing_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "replication.sqlite"
            self._write_fixture_db(db)
            report = audit_table1_denominators(
                db,
                expected_targets={
                    "listings": 3,
                    "sellers": 2,
                    "buyers": 2,
                    "threads": 2,
                    "missing_used_listing_values": 1,
                    "sellers_missing_feedback": 1,
                },
            )
            self.assertTrue(report["passed"], report["target_results"])
            self.assertEqual(report["listing_level"]["used_nonmissing_denominator"], 2)
            self.assertEqual(report["listing_level"]["used_true_numerator"], 1)
            self.assertEqual(report["seller_level"]["feedback_nonmissing_seller_count"], 1)
            self.assertEqual(report["buyer_level"]["buyer_count"], 2)
            self.assertEqual(report["thread_level"]["duplicate_listing_buyer_pairs"], 0)
            self.assertEqual(report["reconciliation_waterfall"][-1]["retained_listings"], 3)
            self.assertTrue(report["restriction_overlap_matrix"])

    @staticmethod
    def _write_fixture_db(path: Path) -> None:
        conn = sqlite3.connect(path)
        try:
            conn.executescript(
                """
                CREATE TABLE listing_sample (
                    listing_id TEXT,
                    seller_id TEXT,
                    buyer_id TEXT,
                    condition_id INTEGER,
                    fdbk_pstv_start REAL,
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
                CREATE TABLE thread_summaries (
                    listing_id TEXT,
                    buyer_id TEXT
                );
                CREATE TABLE buyer_offer_stats (
                    buyer_id TEXT PRIMARY KEY,
                    num_offrs INTEGER,
                    num_threads INTEGER
                );
                """
            )
            conn.executemany(
                "INSERT INTO listing_sample VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("l1", "s1", "b1", 3000, 99.0, 0, 0, 0, 0, 0, 0, 0, 0, 1),
                    ("l2", "s1", "b2", 1000, 99.0, 0, 0, 0, 0, 0, 0, 0, 0, 1),
                    ("l3", "s2", None, None, None, 0, 0, 0, 0, 0, 0, 0, 0, 1),
                    ("l4", "s3", "b3", 3000, 88.0, 1, 0, 0, 0, 0, 0, 0, 0, 0),
                ],
            )
            conn.executemany("INSERT INTO thread_summaries VALUES (?, ?)", [("l1", "b1"), ("l2", "b2"), ("l4", "b3")])
            conn.executemany("INSERT INTO buyer_offer_stats VALUES (?, ?, ?)", [("b1", 1, 1), ("b2", 1, 1), ("b3", 1, 1)])
            conn.commit()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
