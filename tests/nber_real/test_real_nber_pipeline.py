from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
import _bootstrap  # noqa: F401,E402

from behavior_lab.datasets.nber_best_offer.real_normalize import normalize_real_dataset
from behavior_lab.datasets.nber_best_offer.replication import replication_check, validate_replication_targets
from behavior_lab.datasets.nber_best_offer.source_schema import (
    REAL_LISTING_COLUMNS,
    REAL_THREAD_COLUMNS,
    inspect_schema,
    read_csv_header,
    load_real_mapping,
    validate_real_headers,
)


FIXTURES = ROOT / "tests" / "fixtures" / "nber_real_schema"


class RealNberPipelineTests(unittest.TestCase):
    def test_fixture_headers_match_real_contract(self) -> None:
        report = validate_real_headers(
            listings=read_csv_header(FIXTURES / "anon_bo_lists.csv"),
            threads=read_csv_header(FIXTURES / "anon_bo_threads.csv"),
        )
        self.assertTrue(report["valid"])
        self.assertEqual(read_csv_header(FIXTURES / "anon_bo_lists.csv"), REAL_LISTING_COLUMNS)
        self.assertEqual(read_csv_header(FIXTURES / "anon_bo_threads.csv"), REAL_THREAD_COLUMNS)
        schema = inspect_schema()
        self.assertIn("mapping_hash", schema)
        self.assertIn("anon_bo_lists.csv", schema["expected_headers"])

    def test_real_normalize_limit_resume_and_replication_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            output = Path(tmp) / "normalized"
            raw.mkdir()
            (raw / "anon_bo_lists.csv").write_text((FIXTURES / "anon_bo_lists.csv").read_text(encoding="utf-8"), encoding="utf-8")
            (raw / "anon_bo_threads.csv").write_text((FIXTURES / "anon_bo_threads.csv").read_text(encoding="utf-8"), encoding="utf-8")

            stopped = normalize_real_dataset(raw, output, limit_threads=10, bucket_count=4, partition_rows=2, stop_after_thread_pass=True)
            self.assertEqual(stopped["status"], "stopped_after_thread_pass")

            manifest = normalize_real_dataset(raw, output, limit_threads=10, bucket_count=4, partition_rows=2)
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(manifest["tables"]["negotiation_turns"]["rows"], 4)
            self.assertEqual(manifest["tables"]["listings"]["rows"], 3)
            self.assertEqual(manifest["thread_linked_listing_extraction"]["unmatched_listing_ids"], 0)
            self.assertEqual(manifest["lineage"]["raw_source_hashes"]["anon_bo_lists"], manifest["source_files"]["anon_bo_lists"]["sha256"])
            self.assertIn("normalization_manifest_hash", manifest["lineage"])
            self.assertTrue(Path(manifest["tables"]["negotiation_turns"]["partitions"][0]["path"]).exists())

            rerun = normalize_real_dataset(raw, output, limit_threads=10, bucket_count=4, partition_rows=2)
            self.assertTrue(rerun["idempotent_rerun"])

            check = replication_check(output)
            self.assertIn("results", check)
            self.assertTrue(check["passed"])

    def test_replication_targets_are_valid(self) -> None:
        report = validate_replication_targets()
        self.assertTrue(report["valid"], report["errors"])
        self.assertGreaterEqual(report["target_count"], 12)
        self.assertIn("published_descriptive_moment", report["level_counts"])

    def test_real_mapping_manifest_is_json_subset_yaml(self) -> None:
        manifest = load_real_mapping(ROOT / "datasets" / "manifests" / "nber_best_offer_real_mapping.yaml")
        self.assertEqual(manifest["files"]["anon_bo_threads.csv"]["header"], REAL_THREAD_COLUMNS)
        self.assertEqual(manifest["files"]["anon_bo_lists.csv"]["header"], REAL_LISTING_COLUMNS)


if __name__ == "__main__":
    unittest.main()
