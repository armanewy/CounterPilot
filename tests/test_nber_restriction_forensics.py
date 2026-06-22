from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

import _bootstrap  # noqa: F401

from behavior_lab.datasets.nber_best_offer.restriction_forensics import (
    build_thread_restriction_forensics,
    evaluate_thread_rows,
    inspect_thread_restriction_forensics,
)
from behavior_lab.datasets.nber_best_offer.source_schema import REAL_THREAD_COLUMNS


class NberRestrictionForensicsTests(unittest.TestCase):
    def test_thread_flags_use_source_order_and_listing_propagation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw"
            raw.mkdir()
            output = root / "out"
            rows = [
                self._row("listing_t2", "buyer1", "01jan2013 00:00:01", 0, 7),
                self._row("listing_t2", "buyer1", "01jan2013 00:00:02", 2, 7),
                self._row("listing_t2", "buyer1", "01jan2013 00:00:03", 1, 7),
                self._row("listing_t2", "buyer1", "01jan2013 00:00:04", 2, 7),
                self._row("listing_t2", "buyer1", "01jan2013 00:00:05", 1, 2),
                self._row("listing_t2", "buyer1", "01jan2013 00:00:06", 1, 2),
                self._row("listing_t3", "buyer2", "01jan2013 00:00:01", 0, 7),
                self._row("listing_t3", "buyer2", "01jan2013 00:00:02", 1, 2),
                self._row("listing_t4", "buyer3", "01jan2013 00:00:01", 0, 1),
                self._row("listing_t4", "buyer3", "01jan2013 00:00:02", 2, 2),
                self._row("listing_t5", "buyer4", "01jan2013 00:00:01", 0, 7),
                self._row("listing_t5", "buyer4", "01jan2013 00:00:01", 2, 2),
                self._row("listing_t5", "buyer5", "01jan2013 00:00:02", 0, 2),
            ]
            self._write_threads(raw / "anon_bo_threads.csv", rows)
            manifest = build_thread_restriction_forensics(raw, output, bucket_count=3)
            observed = manifest["observed"]
            self.assertEqual(observed["T2_buyer_violation_listing_count"], 1)
            self.assertEqual(observed["T2_seller_violation_listing_count"], 0)
            self.assertEqual(observed["T3_violation_listing_count"], 1)
            self.assertEqual(observed["T4_violation_listing_count"], 1)
            self.assertEqual(observed["T5_violation_listing_count"], 1)
            self.assertEqual(observed["T5_violation_thread_count"], 1)

            listing_rows = self._read_listing_flags(output / "listing_thread_flags.jsonl")
            self.assertTrue(listing_rows["listing_t5"]["T5_violation"])
            self.assertEqual(listing_rows["listing_t5"]["thread_count"], 2)

            inspection = inspect_thread_restriction_forensics(output)
            self.assertTrue(inspection["valid"], inspection["failures"])

    def test_evaluate_thread_rows_preserves_source_order_for_ties(self) -> None:
        rows = [
            {"src_cre_date": "01jan2013 00:00:01", "source_row_ordinal": 2, "offr_type_id": "2", "status_id": "2"},
            {"src_cre_date": "01jan2013 00:00:01", "source_row_ordinal": 1, "offr_type_id": "0", "status_id": "7"},
        ]
        result = evaluate_thread_rows(rows)
        self.assertFalse(result["T3_violation"])
        self.assertTrue(result["T5_violation"])

    @staticmethod
    def _row(listing_id: str, buyer_id: str, timestamp: str, offer_type: int, status: int) -> dict[str, str]:
        row = {column: "" for column in REAL_THREAD_COLUMNS}
        row.update(
            {
                "anon_item_id": listing_id,
                "anon_thread_id": f"{listing_id}-{buyer_id}",
                "anon_byr_id": buyer_id,
                "anon_slr_id": "seller",
                "src_cre_date": timestamp,
                "offr_type_id": str(offer_type),
                "status_id": str(status),
                "offr_price": "10",
            }
        )
        return row

    @staticmethod
    def _write_threads(path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=REAL_THREAD_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _read_listing_flags(path: Path) -> dict[str, dict[str, object]]:
        with path.open("r", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        return {str(row["listing_id"]): row for row in rows}


if __name__ == "__main__":
    unittest.main()
