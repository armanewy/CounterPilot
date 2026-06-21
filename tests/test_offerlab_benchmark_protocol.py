from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import _bootstrap  # noqa: F401,E402


MANIFEST = ROOT / "datasets" / "manifests" / "offerlab_benchmark_v1.yaml"
DOC = ROOT / "docs" / "research" / "OFFERLAB_BENCHMARK_V1.md"


class OfferLabBenchmarkProtocolTests(unittest.TestCase):
    def test_protocol_manifest_is_frozen_research_only_and_complete(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["benchmark_id"], "offerlab_benchmark_v1")
        self.assertEqual(manifest["status"], "frozen")
        self.assertTrue(manifest["research_only"])
        self.assertFalse(manifest["production_export_allowed"])
        self.assertEqual(manifest["source_dataset_ids"], ["nber_ebay_best_offer"])
        self.assertCountEqual(
            manifest["targets"],
            [
                "seller_next_action",
                "buyer_response_to_counter",
                "agreement",
                "final_price_ratio",
                "response_latency",
            ],
        )
        self.assertIn("chronological_listing_purged", {item["name"] for item in manifest["splits"]})
        self.assertIn("seller_disjoint", {item["name"] for item in manifest["splits"]})
        self.assertIn("hidden_lockbox", {item["name"] for item in manifest["splits"]})
        self.assertEqual(manifest["model_selection_rule"]["hidden_queries_per_target"], 1)

    def test_protocol_forbids_known_leakage_aliases_and_identifiers(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        forbidden = set(manifest["forbidden_features"])

        for name in {
            "buyer_id",
            "seller_id",
            "listing_id",
            "thread_id",
            "status_id",
            "event_time",
            "response_time",
            "reference_price",
            "ref_price4",
            "final_sale_price",
            "accept_price",
            "decline_price",
            "accepted_price",
            "label",
        }:
            self.assertIn(name, forbidden)
        self.assertNotIn("event_time", set(manifest["allowed_features"]))

    def test_protocol_document_freezes_hidden_access_and_negative_controls(self) -> None:
        text = DOC.read_text(encoding="utf-8")

        self.assertIn("Do not revise after hidden results", text)
        self.assertIn("one submission per target", text)
        self.assertIn("Random labels", text)
        self.assertIn("Identifier-memorization canaries", text)
        self.assertIn("It cannot support causal claims", text)


if __name__ == "__main__":
    unittest.main()
