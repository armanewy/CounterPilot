from __future__ import annotations

import _bootstrap  # noqa: F401

import tempfile
import unittest
from pathlib import Path

from behavior_lab.core import HypothesisSpec
from behavior_lab.discovery import DiscoveryLoop, LLMHypothesisGenerator
from behavior_lab.experiments import ExperimentScheduler
from behavior_lab.gym import EmptyEvaluationSplit, WorldGym
from behavior_lab.personal_lab import PersonalLab
from behavior_lab.research_api import EvaluationBudgetExceeded, ResearchAPI
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
            self.assertIn(
                assignment["assignment"]["assigned_treatment"],
                {"explicit_first_step", "generic_task_description"},
            )
            lab.capture_trial_outcome(
                assignment,
                started_within_10_minutes=True,
                time_to_start_seconds=120,
                completed_within_day=False,
            )
            effect = lab.estimate_effect(
                "explicit_first_step", "generic_task_description", preregistration_id=prereg
            )
            self.assertEqual(effect["treatment_n"] + effect["comparator_n"], 1)

    def test_personal_lab_refuses_unbound_model_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lab = PersonalLab(Path(tmp))
            with self.assertRaisesRegex(RuntimeError, "ResearchAPI.freeze_candidate"):
                lab.freeze_model_for_prospective_block("unregistered", "test")

    def test_real_intervention_requires_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = ExperimentScheduler(
                WorldGym(Path(tmp), world=HabitPlusOverrideWorld(seed=4)).ledger
            )
            with self.assertRaises(PermissionError):
                scheduler.launch_real_intervention(None, approved_by_human=False)  # type: ignore[arg-type]

    def test_discovery_loop_uses_one_hidden_query_and_true_future_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gym = WorldGym(Path(tmp), world=HabitPlusOverrideWorld(seed=5))
            gym.seed(100)
            report = DiscoveryLoop(gym).run(
                iterations=2,
                offline_trials_per_iteration=3,
                prospective_episodes=12,
            )
            self.assertEqual(len(report["iterations"]), 2)
            self.assertEqual(report["final"]["hidden_submissions"], 1)
            self.assertEqual(report["final"]["prospective_result"]["n"], 12)
            self.assertNotIn("base_rate", report["final"]["hidden_result"])
            self.assertTrue(gym.ledger.verify_hash_chain())
            self.assertEqual(
                len(
                    [
                        item
                        for item in gym.ledger.payloads("evaluation_budget_use")
                        if item.get("split") == "hidden"
                    ]
                ),
                1,
            )

    def test_research_api_facade_redacts_lockbox_prevalence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gym = WorldGym(Path(tmp), world=HabitPlusOverrideWorld(seed=6))
            gym.seed(80)
            api = ResearchAPI(gym)
            self.assertIn("ambiguity", api.list_variables())
            models = api.fit_model_zoo()
            with self.assertRaises(PermissionError):
                api.evaluate_hypothesis(models[0].model_id, split="hidden")
            api.freeze_candidate(models[0].model_id)
            result = api.evaluate_hypothesis(models[0].model_id, split="hidden")
            self.assertIn("redacted", result["details"])
            self.assertNotIn("base_rate", result)
            with self.assertRaises(PermissionError):
                api.compare_models(models[0].model_id, models[1].model_id, split="hidden")
            proposal = api.propose_experiment([model.model_id for model in models[:3]])
            self.assertGreaterEqual(proposal.expected_hypothesis_separation, 0.0)

    def test_split_manifest_does_not_migrate_and_prospective_is_post_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gym = WorldGym(Path(tmp), world=HabitPlusOverrideWorld(seed=7))
            gym.seed(60)
            self.assertFalse(gym.split_assignments())
            api = ResearchAPI(gym, campaign_id="manifest-test")
            original = {
                row["case_id"]: split
                for split, rows in gym.splits("manifest-test").items()
                for row in rows
            }
            gym.seed(5)
            # Existing campaign receives new observations as staging, not training.
            gym.ensure_split_manifest(campaign_id="manifest-test")
            updated = gym.split_assignments("manifest-test")
            for case_id, split in original.items():
                self.assertEqual(updated[case_id], split)
            new_pre_freeze = set(updated) - set(original)
            self.assertTrue(new_pre_freeze)
            self.assertTrue(all(updated[case_id] == "staging" for case_id in new_pre_freeze))

            model = api.fit_model_zoo()[0]
            freeze = api.freeze_candidate(model.model_id)
            with self.assertRaises(EmptyEvaluationSplit):
                api.submit_frozen_candidate(model.model_id)
            gym.seed(2)
            gym.ensure_split_manifest(campaign_id="manifest-test")
            after_freeze = gym.split_assignments("manifest-test")
            new_case_ids = set(after_freeze) - set(updated)
            self.assertTrue(new_case_ids)
            self.assertTrue(all(after_freeze[case_id] == "prospective" for case_id in new_case_ids))
            records = gym.split_assignment_records("manifest-test")
            self.assertTrue(
                all(records[case_id].get("freeze_id") == freeze["payload"]["freeze_id"] for case_id in new_case_ids)
            )
            result = api.submit_frozen_candidate(model.model_id)
            self.assertEqual(result["n"], 2)

    def test_hidden_budget_cannot_be_reset_by_renaming_campaign(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gym = WorldGym(Path(tmp), world=HabitPlusOverrideWorld(seed=8))
            gym.seed(90)
            api = ResearchAPI(gym, campaign_id="budget-test")
            spec = HypothesisSpec.formula(
                "h_budget_reload",
                gym.target_name,
                ["deadline_near", "fatigue", "explicit_first_step * indicator(ambiguity > 0.6)"],
            )
            api.submit_hypothesis(spec)
            fit = api.fit_hypothesis(spec.hypothesis_id)
            model_id = fit["model_id"]
            proposal = api.propose_experiment([model_id])
            before = len(gym.ledger.payloads("intervention_trial"))
            summary = api.run_offline_experiment(proposal, trials=4)
            self.assertTrue(summary["ledger_valid"])
            self.assertEqual(summary["trials_appended"], 4)
            self.assertEqual(len(gym.ledger.payloads("intervention_trial")), before + 4)

            api.freeze_candidate(model_id)
            api.evaluate_hypothesis(model_id, split="hidden")
            with self.assertRaises(EvaluationBudgetExceeded):
                api.evaluate_hypothesis(model_id, split="hidden")

            reloaded = ResearchAPI(gym, campaign_id="budget-test")
            self.assertIn(model_id, reloaded.models)

            renamed = ResearchAPI(gym, campaign_id="renamed-campaign")
            renamed_spec = HypothesisSpec.formula(
                "h_budget_renamed",
                gym.target_name,
                ["deadline_near", "fatigue"],
            )
            renamed.submit_hypothesis(renamed_spec)
            renamed_fit = renamed.fit_hypothesis(renamed_spec.hypothesis_id)
            renamed.freeze_candidate(renamed_fit["model_id"])
            with self.assertRaises(EvaluationBudgetExceeded):
                renamed.evaluate_hypothesis(renamed_fit["model_id"], split="hidden")

    def test_llm_hypothesis_generator_validates_dsl_variables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gym = WorldGym(Path(tmp), world=HabitPlusOverrideWorld(seed=9))
            gym.seed(50)
            api = ResearchAPI(gym, campaign_id="llm-adapter-test")
            generator = LLMHypothesisGenerator(
                lambda _: [
                    {
                        "hypothesis_id": "h_llm_valid",
                        "terms": ["deadline_near", "fatigue"],
                        "assumptions": ["synthetic safe adapter test"],
                        "falsification_conditions": ["fails on development"],
                    }
                ]
            )
            specs = generator.propose(api)
            self.assertEqual(specs[0].hypothesis_id, "h_llm_valid")

            invalid = LLMHypothesisGenerator(lambda _: [{"terms": ["hidden_label_from_future"]}])
            with self.assertRaises(ValueError):
                invalid.propose(api)


if __name__ == "__main__":
    unittest.main()
