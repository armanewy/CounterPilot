from __future__ import annotations

import _bootstrap  # noqa: F401

import tempfile
import unittest
from pathlib import Path

from behavior_lab.core import HypothesisSpec
from behavior_lab.dsl import Formula
from behavior_lab.gym import WorldGym
from behavior_lab.models import LogisticFormulaHypothesis, ModelFoundry
from behavior_lab.worlds import HabitPlusOverrideWorld


class FormulaFoundryTests(unittest.TestCase):
    def test_formula_dsl_and_fit(self) -> None:
        term = Formula.parse(["explicit_first_step * indicator(ambiguity > 0.6)"])
        self.assertEqual(term.vector({"explicit_first_step": 1.0, "ambiguity": 0.8}), [1.0, 1.0])
        self.assertEqual(term.vector({"explicit_first_step": 1.0, "ambiguity": 0.2}), [1.0, 0.0])

    def test_model_zoo_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gym = WorldGym(Path(tmp), world=HabitPlusOverrideWorld(seed=3))
            gym.seed(90)
            splits = gym.splits()
            models = ModelFoundry().fit_zoo(splits["training"], splits["development"], "started_within_10_minutes")
            self.assertGreaterEqual(len(models), 6)
            spec = HypothesisSpec.formula(
                "h_test",
                "started_within_10_minutes",
                ["explicit_first_step", "ambiguity", "explicit_first_step * indicator(ambiguity > 0.6)"],
            )
            fitted = LogisticFormulaHypothesis(spec).fit(splits["training"])
            probability = fitted.predict_proba(splits["development"][0]["features"])
            self.assertGreater(probability, 0.0)
            self.assertLess(probability, 1.0)


if __name__ == "__main__":
    unittest.main()
