from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "datasets" / "manifests" / "nber_replication_targets.yaml"
CONTRACT = ROOT / "docs" / "research" / "NBER_REPLICATION_CONTRACT.md"


class NberReplicationTargetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.targets = cls.manifest["targets"]
        cls.by_id = {target["id"]: target for target in cls.targets}

    def test_manifest_records_official_sources_without_raw_data(self) -> None:
        self.assertFalse(self.manifest["raw_data_policy"]["raw_csv_downloaded"])
        skipped_urls = {entry["url"] for entry in self.manifest["raw_data_policy"]["large_raw_files_not_downloaded"]}
        self.assertIn("https://nber.org/bargaining/anon_bo_lists.csv.gz", skipped_urls)
        self.assertIn("https://nber.org/bargaining/anon_bo_threads.csv.gz", skipped_urls)
        self.assertIn("https://nber.org/bargaining/bargaining_data.zip", skipped_urls)

        source_ids = [source["id"] for source in self.manifest["source_artifacts"]]
        self.assertEqual(len(source_ids), len(set(source_ids)))
        for source in self.manifest["source_artifacts"]:
            self.assertRegex(source["sha256"], r"^[A-F0-9]{64}$")
            self.assertTrue(source["path"])
            self.assertTrue(source["url"])

    def test_targets_cover_required_levels_and_fields(self) -> None:
        self.assertGreaterEqual(len(self.targets), 12)
        self.assertEqual(len(self.by_id), len(self.targets))
        self.assertEqual(
            {target["level"] for target in self.targets},
            {"structural_invariant", "published_descriptive_moment", "nonfatal_diagnostic_moment"},
        )

        source_ids = {source["id"] for source in self.manifest["source_artifacts"]}
        for target in self.targets:
            with self.subTest(target=target["id"]):
                for key in (
                    "id",
                    "level",
                    "status",
                    "source_refs",
                    "source_detail",
                    "population_filters",
                    "formula",
                    "expected",
                    "tolerance",
                    "known_differences",
                ):
                    self.assertIn(key, target)
                self.assertTrue(target["source_detail"])
                self.assertTrue(target["population_filters"])
                self.assertTrue(target["formula"])
                self.assertTrue(target["expected"])
                self.assertTrue(target["tolerance"])
                self.assertTrue(target["known_differences"])
                self.assertTrue(set(target["source_refs"]).issubset(source_ids))
                if target["level"] == "nonfatal_diagnostic_moment":
                    self.assertEqual(target["status"], "nonfatal")
                else:
                    self.assertEqual(target["status"], "fatal")

    def test_representative_published_constants_are_frozen(self) -> None:
        expected_values = {
            "struct_raw_listings_before_restrictions": ("value", 98307281),
            "struct_main_sample_listings_after_restrictions": ("value", 88388220),
            "pub_table1_thread_count": ("value", 25458516),
            "diag_ref_sample_listing_count": ("value", 2047079),
            "diag_figure4_root_game_tree_count": ("value", 25117275),
        }
        for target_id, (field, expected) in expected_values.items():
            with self.subTest(target=target_id):
                self.assertEqual(self.by_id[target_id]["expected"][field], expected)

        self.assertEqual(self.by_id["struct_l1_price_over_1000_exclusions"]["expected"]["count"], 9547987)
        self.assertEqual(self.by_id["struct_l2_sale_price_above_listing_exclusions"]["expected"]["count"], 42524)
        self.assertEqual(self.by_id["struct_t2_offer_limit_exclusions"]["expected"]["seller_count"], 0)
        self.assertEqual(self.by_id["pub_table1_listing_bargained_price_to_list"]["expected"]["value"], 0.727)
        self.assertEqual(self.by_id["pub_table1_thread_agreement_rate"]["expected"]["value"], 0.454)
        self.assertEqual(self.by_id["diag_figure4_first_seller_response_shares"]["expected"]["decline_share"], 0.40)

    def test_contract_document_matches_manifest_targets(self) -> None:
        text = CONTRACT.read_text(encoding="utf-8")
        for target_id in self.by_id:
            with self.subTest(target=target_id):
                self.assertIn(f"`{target_id}`", text)

        required_phrases = [
            "Raw data policy",
            "raw `anon_bo_lists.csv.gz`, `anon_bo_threads.csv.gz`, and",
            "Released 2019 `paper_sample.do` adds T5",
            "Gate rule",
            "Leakage Risks",
            "does not process raw CSVs",
        ]
        for phrase in required_phrases:
            self.assertIn(phrase, text)

    def test_manifest_is_ascii_json_subset_yaml(self) -> None:
        raw = MANIFEST.read_text(encoding="utf-8")
        self.assertEqual(raw.encode("ascii").decode("ascii"), raw)
        self.assertIsInstance(json.loads(raw), dict)
        self.assertIsNone(re.search(r"\bNaN\b|\bInfinity\b", raw))


if __name__ == "__main__":
    unittest.main()
