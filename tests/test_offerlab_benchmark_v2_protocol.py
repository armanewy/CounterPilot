from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import _bootstrap  # noqa: F401,E402


V1_FINAL = ROOT / "reports" / "offerlab_benchmark_v1_final_manifest.json"
V2_MANIFEST = ROOT / "datasets" / "manifests" / "offerlab_benchmark_v2.yaml"
V2_DOC = ROOT / "docs" / "research" / "OFFERLAB_BENCHMARK_V2.md"


class OfferLabBenchmarkV2ProtocolTests(unittest.TestCase):
    def test_v1_final_manifest_permanently_freezes_hidden_spent_benchmark(self) -> None:
        manifest = json.loads(V1_FINAL.read_text(encoding="utf-8"))

        self.assertEqual(manifest["benchmark_id"], "offerlab_benchmark_v1")
        self.assertEqual(manifest["status"], "frozen")
        self.assertEqual(manifest["hidden_status"], "hidden_spent")
        self.assertEqual(manifest["reuse_policy"], "never_reusable_for_model_selection")
        self.assertEqual(manifest["final_decision"]["status"], "STOP")
        self.assertFalse(manifest["v2_implications"]["repeat_v1_allowed"])
        self.assertTrue(manifest["v2_implications"]["fresh_hidden_cases_required"])

    def test_v1_manifest_records_all_hidden_queries_and_token_availability(self) -> None:
        manifest = json.loads(V1_FINAL.read_text(encoding="utf-8"))
        lockbox = manifest["hidden_lockbox"]

        self.assertEqual(lockbox["canonical_store_name"], "hidden_lockbox_offerlab_benchmark_v1_4717f92cdb18.jsonl")
        self.assertEqual(lockbox["event_count_reported"], 5)
        self.assertEqual(len(lockbox["queries"]), 5)
        self.assertEqual({query["hidden_submission_count"] for query in lockbox["queries"]}, {1})
        self.assertFalse(lockbox["case_tokens"]["tokens"])
        self.assertIn("block v2 hidden lockbox creation", lockbox["case_tokens"]["required_v2_behavior"])

    def test_v2_requires_full_release_and_all_protocol_splits(self) -> None:
        manifest = json.loads(V2_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["benchmark_id"], "offerlab_benchmark_v2")
        self.assertEqual(manifest["v1_relationship"], "new_benchmark_not_repeat")
        self.assertTrue(manifest["research_only"])
        self.assertFalse(manifest["production_export_allowed"])
        self.assertTrue(manifest["required_normalization"]["full_release_required"])
        self.assertTrue(manifest["required_normalization"]["streaming_required"])
        self.assertFalse(manifest["required_normalization"]["model_row_cap_allowed"])

        split_names = {split["name"] for split in manifest["splits"]}
        self.assertSetEqual(
            split_names,
            {
                "chronological_listing_purged",
                "seller_disjoint",
                "buyer_disjoint",
                "category_disjoint_diagnostic",
                "thread_safe_nested_development",
                "fresh_hidden_lockbox",
            },
        )

    def test_v2_hidden_policy_blocks_reuse_or_overlap_with_v1(self) -> None:
        manifest = json.loads(V2_MANIFEST.read_text(encoding="utf-8"))
        hidden = manifest["hidden_policy"]

        self.assertEqual(hidden["hidden_queries_per_target"], 1)
        self.assertTrue(hidden["fresh_hidden_lockbox_required"])
        self.assertTrue(hidden["exclude_all_v1_hidden_case_tokens"])
        self.assertTrue(hidden["block_hidden_creation_if_v1_tokens_unavailable"])
        self.assertFalse(hidden["protocol_changes_after_hidden_access_allowed"])

    def test_v2_requires_calibration_coverage_controls_and_censored_label_handling(self) -> None:
        manifest = json.loads(V2_MANIFEST.read_text(encoding="utf-8"))

        self.assertTrue(manifest["calibration_acceptance"]["must_be_declared_before_hidden_access"])
        self.assertLessEqual(manifest["calibration_acceptance"]["classification"]["expected_calibration_error_max"], 0.08)
        self.assertGreaterEqual(manifest["support_coverage"]["primary_candidate_minimum"], 0.8)
        self.assertTrue(manifest["missing_and_censored_label_policy"]["preserve_unknown_outcomes"])
        self.assertTrue(manifest["missing_and_censored_label_policy"]["preserve_censored_outcomes"])
        self.assertFalse(manifest["missing_and_censored_label_policy"]["convert_censored_to_rejection_allowed"])

        controls = set(manifest["negative_controls"])
        for name in {
            "random_labels",
            "future_status_canary",
            "accepted_price_canary",
            "identifier_memorization_canary",
            "random_row_split_inflation",
            "same_timestamp_ordering_perturbation",
            "censoring_as_rejection_canary",
            "artifact_name_leakage_canary",
        }:
            self.assertIn(name, controls)

    def test_v2_doc_refuses_production_and_v1_repeat_claims(self) -> None:
        text = V2_DOC.read_text(encoding="utf-8")

        self.assertIn("It is not a repeat of Benchmark v1", text)
        self.assertIn("never_reusable_for_model_selection", text)
        self.assertIn("It may never return production-ready based on NBER data", text)
        self.assertIn("one hidden submission per target", text)


if __name__ == "__main__":
    unittest.main()
