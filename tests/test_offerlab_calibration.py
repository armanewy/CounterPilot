from __future__ import annotations

import _bootstrap  # noqa: F401

import unittest

from behavior_lab.offerlab_models.calibration import (
    action_level_sample_counts,
    bootstrap_brier_uncertainty,
    calibration_by_slices,
    final_price_prediction_interval,
    isotonic_calibrate,
    reliability_diagram,
    sigmoid_calibrate,
    temporal_drift,
)


class OfferLabCalibrationTests(unittest.TestCase):
    def test_sigmoid_and_isotonic_calibrators_return_probabilities(self) -> None:
        probabilities = [0.1, 0.3, 0.7, 0.9]
        outcomes = [0, 0, 1, 1]
        sigmoid = sigmoid_calibrate(probabilities, outcomes, iterations=20)
        isotonic = isotonic_calibrate(probabilities, outcomes)
        self.assertEqual(len(sigmoid["calibrated"]), 4)
        self.assertEqual(len(isotonic["calibrated"]), 4)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in sigmoid["calibrated"]))
        self.assertTrue(all(0.0 <= value <= 1.0 for value in isotonic["calibrated"]))

    def test_reliability_slices_drift_uncertainty_and_intervals(self) -> None:
        rows = [
            {"row_id": "r1", "label": "accept", "timestamp": "2020-01-01T00:00:00", "features": {"category": "cameras", "offer_to_asking_ratio": 0.9}},
            {"row_id": "r2", "label": "decline", "timestamp": "2020-01-02T00:00:00", "features": {"category": "cameras", "offer_to_asking_ratio": 0.5}},
            {"row_id": "r3", "label": "accept", "timestamp": "2020-01-03T00:00:00", "features": {"category": "parts", "offer_to_asking_ratio": 0.8}},
        ]
        predictions = [
            {"row_id": "r1", "label": "accept", "probabilities": {"accept": 0.8}},
            {"row_id": "r2", "label": "decline", "probabilities": {"accept": 0.2}},
            {"row_id": "r3", "label": "accept", "probabilities": {"accept": 0.6}},
        ]
        self.assertIn("expected_calibration_error", reliability_diagram(predictions))
        self.assertIn("category", calibration_by_slices(predictions, rows))
        self.assertIn("absolute_drift", temporal_drift(predictions, rows))
        self.assertIn("confidence_interval", bootstrap_brier_uncertainty(predictions, samples=20))
        self.assertEqual(action_level_sample_counts(rows), {"accept": 2, "decline": 1})
        interval = final_price_prediction_interval([{"label": 0.7}, {"label": 0.8}], 0.75)
        self.assertLessEqual(interval["lower"], 0.75)
        self.assertGreaterEqual(interval["upper"], 0.75)


if __name__ == "__main__":
    unittest.main()
