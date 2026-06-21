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
            board = benchmark(normalized)
            self.assertIn("seller_next_action", board["leaderboards"])
            report = audit(normalized)
            self.assertTrue(all(report["leakage_checks"].values()))

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
