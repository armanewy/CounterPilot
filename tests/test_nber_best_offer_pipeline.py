from __future__ import annotations

import _bootstrap  # noqa: F401

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from behavior_lab.datasets.nber_best_offer.audit import audit, benchmark
from behavior_lab.datasets.nber_best_offer.normalize import build_sample_dataset, normalize_dataset
from behavior_lab.datasets.nber_best_offer.tasks import assert_no_future_leakage, build_tasks


class NberBestOfferPipelineTests(unittest.TestCase):
    def test_sample_normalize_tasks_benchmark_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            normalized = Path(tmp) / "normalized"
            build_sample_dataset(raw)
            manifest = normalize_dataset(raw, normalized)
            self.assertEqual(manifest["tables"]["listings"]["rows"], 3)
            tasks = build_tasks(normalized)
            self.assertGreaterEqual(len(tasks["seller_next_action"]), 3)
            self.assertTrue(assert_no_future_leakage(tasks["seller_next_action"]))
            first = tasks["seller_next_action"][0]
            self.assertNotIn("seller_id", first["features"])
            self.assertNotIn("buyer_id", first["features"])
            self.assertNotIn("status", first["observed_history"][0])
            self.assertNotIn("event_time", first["observed_history"][0])
            board = benchmark(normalized)
            self.assertIn("seller_next_action", board["leaderboards"])
            self.assertIn("chronological", board["leaderboards"]["seller_next_action"])
            self.assertIn("seller_disjoint", board["leaderboards"]["seller_next_action"])
            self.assertFalse(board["scope"]["full_release_evidence"])
            self.assertEqual(board["scope"]["evidence_scope"], "bounded_smoke_or_semantics")
            report = audit(normalized)
            self.assertTrue(report["dataset_dir"].startswith("local_normalized_dir_hash:"))
            self.assertTrue(all(report["leakage_checks"].values()))
            self.assertIn("splits", report["tasks"]["seller_next_action"])

    def test_full_release_scope_requires_structured_audited_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            normalized = Path(tmp) / "normalized"
            build_sample_dataset(raw)
            normalize_dataset(raw, normalized)
            manifest_path = normalized / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["command_args"] = {"full": True, "limit_threads": None}
            manifest["full_release_gate_passed"] = True
            manifest["full_release_evidence_passed"] = True
            manifest["audited_full_release_evidence"] = True
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            board = benchmark(normalized)

            self.assertFalse(board["scope"]["full_release_evidence"])
            self.assertFalse(board["scope"]["full_release_gate_passed"])
            self.assertEqual(board["scope"]["evidence_scope"], "bounded_smoke_or_semantics")

    def test_full_release_scope_rejects_handwritten_gate_without_official_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            normalized = Path(tmp) / "normalized"
            build_sample_dataset(raw)
            normalize_dataset(raw, normalized)
            manifest_path = normalized / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["command_args"] = {"full": True, "limit_threads": None}
            manifest["audited_full_release_evidence"] = {
                "passed": True,
                "replication_contract_passed": True,
                "streaming_full_run_passed": True,
                "official_sources_matched": True,
                "full_run_checkpoint_validated": True,
                "partition_hashes_verified": True,
                "independent_audit_passed": True,
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            board = benchmark(normalized)

            self.assertFalse(board["scope"]["full_release_evidence"])
            self.assertFalse(board["scope"]["full_release_gate_passed"])
            self.assertIn("official_contract_matches", board["scope"]["full_release_gate_failures"])
            self.assertIn("source_files_match_official_contract", board["scope"]["full_release_gate_failures"])
            self.assertEqual(board["scope"]["evidence_scope"], "bounded_smoke_or_semantics")

    def test_leakage_audit_rejects_status_in_observed_history(self) -> None:
        self.assertFalse(assert_no_future_leakage([{"features": {}, "observed_history": [{"status": "accepted"}]}]))

    def test_cli_nber_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            normalized = Path(tmp) / "normalized"
            env = dict(os.environ)
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
            subprocess.run(
                [sys.executable, "-m", "behavior_lab", "nber-best-offer", "build-sample", "--output-dir", str(raw)],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            subprocess.run(
                [sys.executable, "-m", "behavior_lab", "nber-best-offer", "normalize", "--input-dir", str(raw), "--output-dir", str(normalized)],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            output = subprocess.run(
                [sys.executable, "-m", "behavior_lab", "nber-best-offer", "audit", "--normalized-dir", str(normalized)],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("leakage_checks", json.loads(output.stdout))


if __name__ == "__main__":
    unittest.main()
