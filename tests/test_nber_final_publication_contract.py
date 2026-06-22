from __future__ import annotations

import json
from pathlib import Path
import unittest

import _bootstrap  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "datasets" / "manifests" / "nber_final_publication_contract.json"
DOC = ROOT / "docs" / "research" / "NBER_FINAL_PUBLICATION_CONTRACT.md"


class NberFinalPublicationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        cls.targets = cls.contract["final_publication_targets"]

    def test_final_publication_targets_are_pinned(self) -> None:
        expected = {
            "source_listings": 98307281,
            "final_listings": 88386471,
            "sellers": 1197397,
            "buyers": 4701301,
            "threads": 25453072,
            "T2_buyer_violations": 3518,
            "T2_seller_violations": 0,
            "T3_missing_counter_violations": 1451,
            "T4_accepted_not_last_violations": 1109,
            "T5_duplicate_timestamp_violations": 4273,
            "missing_used_values": 27678157,
            "sellers_with_missing_feedback": 51992,
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(self.targets[key], value)

    def test_working_paper_values_are_not_final_targets(self) -> None:
        stale_values = {"88388220", "3529", "1453", "1111"}
        final_values = {str(value) for value in self.targets.values()}
        self.assertTrue(stale_values.isdisjoint(final_values))

        working = self.contract["working_paper_targets"]
        self.assertEqual(working["main_sample_listings_without_T5"], 88388220)
        self.assertEqual(working["T2_buyer_violations"], 3529)
        self.assertEqual(working["T3_missing_counter_violations"], 1453)
        self.assertEqual(working["T4_accepted_not_last_violations"], 1111)
        self.assertIsNone(working["T5_duplicate_timestamp_violations"])

    def test_t5_and_lower_bound_resolution_are_explicit(self) -> None:
        lower = self.contract["lower_listing_price_boundary"]
        self.assertIn("No lower listing-price boundary", lower["final_contract"])
        self.assertIn("start_price_usd > 1000", lower["released_code_evidence"])
        self.assertIn("0.99", lower["implementation_rule"])

        table_text = json.dumps(self.contract["version_difference_table"], sort_keys=True)
        self.assertIn("T5", table_text)
        self.assertIn("4273", table_text)

    def test_stale_repository_values_are_reported(self) -> None:
        stale = self.contract["stale_repository_values"]
        observed = {(item["path"], str(item["value"])) for item in stale}
        required = {
            ("datasets/manifests/nber_replication_targets.yaml", "88388220"),
            ("datasets/manifests/nber_replication_targets.yaml", "3529"),
            ("datasets/manifests/nber_replication_targets.yaml", "1453"),
            ("datasets/manifests/nber_replication_targets.yaml", "1111"),
            ("docs/research/NBER_REPLICATION_CONTRACT.md", "88,388,220"),
        }
        self.assertTrue(required.issubset(observed))

    def test_document_matches_contract(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        for phrase in [
            "88,386,471",
            "3,518",
            "1,451",
            "1,109",
            "4,273",
            "27,678,157",
            "51,992",
            "Do not impose a lower bound",
        ]:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
