from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

import _bootstrap  # noqa: F401

from behavior_lab.datasets.nber_best_offer.full_listing_pass import (
    build_full_listing_restrictions,
    inspect_full_listing_restrictions,
)
from behavior_lab.datasets.nber_best_offer.source_schema import REAL_LISTING_COLUMNS


class NberFullListingPassTests(unittest.TestCase):
    def test_streams_listing_restrictions_without_missing_value_coercion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw"
            raw.mkdir()
            output = root / "out"
            self._write_listings(
                raw / "anon_bo_lists.csv",
                [
                    self._row("l1", "s1", start_price="0.01", item_price="", condition="", feedback="99.5"),
                    self._row("l2", "s1", start_price="1001", item_price="", condition="3000", feedback=""),
                    self._row("l3", "s2", start_price="100", item_price="120", condition="1000", feedback="97"),
                    self._row("l3", "s3", start_price="80", item_price="", condition="3000", feedback="90"),
                ],
            )

            manifest = build_full_listing_restrictions(raw, output, partitions=3)
            summary = manifest["summary"]
            self.assertEqual(summary["raw_source_listing_count"], 4)
            self.assertEqual(summary["accepted_unique_listing_count"], 3)
            self.assertEqual(summary["duplicate_listing_id_count"], 1)
            self.assertEqual(summary["L1_violation_count"], 1)
            self.assertEqual(summary["L2_violation_count"], 1)
            self.assertEqual(summary["used_nonmissing_denominator"], 2)
            self.assertEqual(summary["used_true_numerator"], 1)
            self.assertEqual(summary["used_missing_count"], 1)
            self.assertEqual(summary["listing_price_distribution"]["min"], 0.01)
            self.assertEqual(summary["seller_count_before_l1_l2"], 2)
            self.assertEqual(summary["seller_count_after_l1_l2"], 1)

            rows = self._read_partition_rows(manifest)
            row_l1 = next(row for row in rows if row["listing_id"] == "l1")
            self.assertFalse(row_l1["L1_violation"])
            self.assertIsNone(row_l1["used"])
            self.assertIsNone(row_l1["T1_violation"])
            self.assertEqual(row_l1["restriction_contract_version"], "2020_qje_final_released_code_v1")

            inspection = inspect_full_listing_restrictions(output)
            self.assertTrue(inspection["valid"], inspection["failures"])

    def test_idempotent_rerun_reuses_verified_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw"
            raw.mkdir()
            output = root / "out"
            self._write_listings(raw / "anon_bo_lists.csv", [self._row("l1", "s1", start_price="10")])
            first = build_full_listing_restrictions(raw, output, partitions=2)
            second = build_full_listing_restrictions(raw, output, partitions=2)
            self.assertEqual(first["signature"], second["signature"])
            self.assertTrue(second["idempotent_rerun"])

    @staticmethod
    def _row(
        listing_id: str,
        seller_id: str,
        *,
        start_price: str = "10",
        item_price: str = "",
        condition: str = "3000",
        feedback: str = "99",
    ) -> dict[str, str]:
        row = {column: "" for column in REAL_LISTING_COLUMNS}
        row.update(
            {
                "anon_item_id": listing_id,
                "anon_slr_id": seller_id,
                "start_price_usd": start_price,
                "item_price": item_price,
                "item_cndtn_id": condition,
                "fdbk_pstv_start": feedback,
            }
        )
        return row

    @staticmethod
    def _write_listings(path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=REAL_LISTING_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _read_partition_rows(manifest: dict[str, object]) -> list[dict[str, object]]:
        rows = []
        for partition in manifest["table"]["partitions"]:  # type: ignore[index]
            path = Path(partition["path"])  # type: ignore[index]
            with path.open("r", encoding="utf-8") as handle:
                rows.extend(json.loads(line) for line in handle if line.strip())
        return rows


if __name__ == "__main__":
    unittest.main()
