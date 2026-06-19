from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from behavior_lab.discovery import DiscoveryLoop
from behavior_lab.experiments import ExperimentScheduler
from behavior_lab.gym import WorldGym
from behavior_lab.personal_lab import PersonalLab
from behavior_lab.research_api import ResearchAPI
from behavior_lab.worlds import HabitPlusOverrideWorld


class ExperimentDiscoveryTests(unittest.TestCase):
    def test_randomized_assignment_and_effect_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lab = PersonalLab(Path(tmp))
            prereg = lab.preregister_task_start_experiment(planned_trials=4)
            context = {"fatigue": 0.4, "ambiguity": 0.8, "task_size_large": 1.0}
            assignment = lab.assign_for_task(
                context,
                treatment="explicit_first_step",
                comparator="generic_task_description",
                preregistration_id=prereg,
            )
            self.assertIn(assignment["assignment"]["assigned_treatment"], {"explicit_first_step", "generic_task_description"})
            lab.capture_trial_outcome(
                assignment,
                started_within_10_minutes=True,
                time_to_start_seconds=120,
                completed_within_day=False,
            )
            effect = lab.estimate_effect("explicit_first_step", "generic_task_description")
            self.assertEqual(effect["treatment_n"] + effect["comparator_n"], 1)

    def test_real_intervention_requires_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = ExperimentScheduler(WorldGym(Path(tmp), world=HabitPlusOverrideWorld(seed=4)).ledger)
            with self.assertRaises(PermissionError):
                scheduler.launch_real_intervention(None, approved_by_human=False)  # type: ignore[arg-type]

    def test_discovery_loop_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gym = WorldGym(Path(tmp), world=HabitPlusOverrideWorld(seed=5))
            gym.seed(120)
            report = DiscoveryLoop(gym).run(iterations=2, offline_trials_per_iteration=3)
            self.assertEqual(len(report["iterations"]), 2)
            self.assertTrue(gym.ledger.verify_hash_chain())
            self.assertGreater(len(gym.ledger.payloads("intervention_trial")), 0)

    def test_research_api_facade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gym = WorldGym(Path(tmp), world=HabitPlusOverrideWorld(seed=6))
            gym.seed(80)
            api = ResearchAPI(gym)
            self.assertIn("ambiguity", api.list_variables())
            models = api.fit_model_zoo()
            result = api.gym.blind_server().evaluate(models[0], split="hidden")
            self.assertEqual(result["details"]["redacted"], "hidden labels and failure rows are not exposed")
            proposal = api.propose_experiment([model.model_id for model in models[:3]])
            self.assertGreaterEqual(proposal.expected_hypothesis_separation, 0.0)


if __name__ == "__main__":
    unittest.main()
