from __future__ import annotations

import _bootstrap  # noqa: F401

from pathlib import Path
import unittest

from behavior_lab.offerlab_models.transfer import evaluate_transfer_ablation


class OfferLabTransferTests(unittest.TestCase):
    def test_default_transfer_is_negative_and_disallows_raw_pooling(self) -> None:
        report = evaluate_transfer_ablation()
        self.assertFalse(report["retained"])
        self.assertFalse(report["raw_pooling_allowed"])
        self.assertFalse(report["production_export_allowed"])

    def test_transfer_retained_only_when_hidden_and_calibration_improve(self) -> None:
        retained = evaluate_transfer_ablation(
            base_hidden_loss=0.7,
            transfer_hidden_loss=0.68,
            base_calibration_error=0.1,
            transfer_calibration_error=0.08,
        )
        not_retained = evaluate_transfer_ablation(
            base_hidden_loss=0.7,
            transfer_hidden_loss=0.68,
            base_calibration_error=0.1,
            transfer_calibration_error=0.12,
        )
        self.assertTrue(retained["retained"])
        self.assertFalse(not_retained["retained"])
        self.assertTrue(Path("docs/TRANSFER_RESULTS.md").exists())


if __name__ == "__main__":
    unittest.main()
