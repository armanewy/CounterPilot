from __future__ import annotations

import _bootstrap  # noqa: F401

import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path

from behavior_lab.core import HypothesisSpec
from behavior_lab.dsl import Formula, FormulaSyntaxError, MAX_TERMS
from behavior_lab.experiments import (
    DuplicateTrialOutcomeError,
    ExperimentIntegrityError,
    ExperimentLimitError,
    ExperimentScheduler,
)
from behavior_lab.gym import SplitManifestError, WorldConfigurationError, WorldGym
from behavior_lab.ledger import ImmutableLedger
from behavior_lab.models import LogisticFormulaHypothesis, model_from_artifact, model_to_artifact
from behavior_lab.worlds import HabitPlusOverrideWorld, ThresholdPersonWorld


class LockboxAndIntegrityTests(unittest.TestCase):
    def test_batch_append_is_hash_chained_and_concurrent_appends_survive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ImmutableLedger(Path(tmp) / "ledger.jsonl")
            ledger.append_many_guarded(
                [("batch", {"index": index}, f"batch_{index}") for index in range(25)],
                unique_record_ids=True,
            )

            errors: list[BaseException] = []

            def worker(worker_id: int) -> None:
                try:
                    for item in range(10):
                        ledger.append(
                            "thread",
                            {"worker": worker_id, "item": item},
                            record_id=f"thread_{worker_id}_{item}",
                            unique_record_id=True,
                        )
                except BaseException as exc:  # pragma: no cover - reported below
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(worker_id,)) for worker_id in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertFalse(errors)
            self.assertEqual(len(ledger.scan()), 65)
            self.assertTrue(ledger.verify_hash_chain())

    def test_world_resume_is_monotonic_and_configuration_is_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            first = WorldGym(path, world=HabitPlusOverrideWorld(seed=12))
            first.seed(8)
            initial_times = [episode["decision_time"] for episode in first.decision_episodes()]

            reopened = WorldGym(path, world=HabitPlusOverrideWorld(seed=12))
            reopened.seed(3)
            all_times = [episode["decision_time"] for episode in reopened.decision_episodes()]
            self.assertEqual(len(all_times), 11)
            self.assertEqual(len(set(all_times)), 11)
            self.assertGreater(min(all_times[8:]), max(initial_times))

            with self.assertRaises(WorldConfigurationError):
                WorldGym(path, world=ThresholdPersonWorld(seed=12))
            with self.assertRaises(WorldConfigurationError):
                WorldGym(path, world=HabitPlusOverrideWorld(seed=13))

    def test_conflicting_split_assignment_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gym = WorldGym(Path(tmp), world=HabitPlusOverrideWorld(seed=3))
            gym.seed(20)
            gym.splits("conflict")
            case_id = next(iter(gym.split_assignments("conflict")))
            # Simulate a corrupted/legacy writer that used a different record ID.
            gym.ledger.append(
                "split_assignment",
                {
                    "case_id": case_id,
                    "episode_id": case_id,
                    "split": "prospective",
                    "campaign_id": "conflict",
                    "assigned_at": "2026-01-01T00:00:00+00:00",
                    "split_policy_version": "corrupt-test",
                },
                record_id="conflicting_assignment",
            )
            with self.assertRaises(SplitManifestError):
                gym.split_assignments("conflict")

    def test_model_artifact_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gym = WorldGym(Path(tmp), world=HabitPlusOverrideWorld(seed=5))
            gym.seed(50)
            rows = gym.splits("artifact")["training"]
            spec = HypothesisSpec.formula(
                "h_artifact",
                gym.target_name,
                ["fatigue", "deadline_near"],
            )
            model = LogisticFormulaHypothesis(spec).fit(rows)
            artifact = model_to_artifact(model, rows)
            restored = model_from_artifact(artifact)
            self.assertEqual(restored.model_id, model.model_id)
            tampered = copy.deepcopy(artifact)
            tampered["weights"][0] += 10
            with self.assertRaises(ValueError):
                model_from_artifact(tampered)

    def test_formula_dsl_enforces_resource_and_syntax_limits(self) -> None:
        with self.assertRaises(FormulaSyntaxError):
            Formula.parse([f"x{index}" for index in range(MAX_TERMS + 1)])
        with self.assertRaises(FormulaSyntaxError):
            Formula.parse(["__import__('os').system('echo bad')"])
        with self.assertRaises(FormulaSyntaxError):
            Formula.parse(["x"] * 2)


    def test_trial_outcome_cannot_tamper_with_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gym = WorldGym(Path(tmp), world=HabitPlusOverrideWorld(seed=10))
            scheduler = ExperimentScheduler(gym.ledger, seed=2)
            prereg = scheduler.preregister(
                question="tamper test",
                treatment="explicit_first_step",
                comparator="generic_task_description",
                target="started_within_10_minutes",
                population="tasks",
                planned_trials=2,
                stopping_rule="fixed",
                analysis_plan="difference in means",
                approval_required=False,
            )
            assignment = scheduler.assign_intervention(
                {"fatigue": 0.2},
                treatment="explicit_first_step",
                comparator="generic_task_description",
                preregistration_id=prereg,
            )
            tampered = copy.deepcopy(assignment)
            current = tampered["assignment"]["assigned_treatment"]
            tampered["assignment"]["assigned_treatment"] = (
                "generic_task_description" if current == "explicit_first_step" else "explicit_first_step"
            )
            with self.assertRaises(ExperimentIntegrityError):
                scheduler.record_trial_outcome(tampered, {"started_within_10_minutes": True})

    def test_preregistration_limit_and_duplicate_outcome_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gym = WorldGym(Path(tmp), world=HabitPlusOverrideWorld(seed=9))
            scheduler = ExperimentScheduler(gym.ledger, seed=1)
            prereg = scheduler.preregister(
                question="fixed sample",
                treatment="explicit_first_step",
                comparator="generic_task_description",
                target="started_within_10_minutes",
                population="tasks",
                planned_trials=1,
                stopping_rule="one assignment",
                analysis_plan="difference in means",
                approval_required=False,
            )
            assignment = scheduler.assign_intervention(
                {"fatigue": 0.2},
                treatment="explicit_first_step",
                comparator="generic_task_description",
                preregistration_id=prereg,
            )
            with self.assertRaises(ExperimentLimitError):
                scheduler.assign_intervention(
                    {"fatigue": 0.3},
                    treatment="explicit_first_step",
                    comparator="generic_task_description",
                    preregistration_id=prereg,
                )
            scheduler.record_trial_outcome(assignment, {"started_within_10_minutes": True})
            with self.assertRaises(DuplicateTrialOutcomeError):
                scheduler.record_trial_outcome(assignment, {"started_within_10_minutes": False})


if __name__ == "__main__":
    unittest.main()
