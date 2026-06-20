from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from behavior_lab.core import DecisionEpisode, utc_now
from behavior_lab.experiments import ExperimentProposal, ExperimentScheduler
from behavior_lab.ledger import ImmutableLedger


class PersonalLab:
    """Local N-of-1 behavior lab for one repeated behavior.

    This is an instrumentation boundary rather than a background monitor. External
    adapters can call these methods with OS, calendar, wearable, or manual data.
    """

    def __init__(self, data_dir: str | Path, subject_id: str = "arman"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = ImmutableLedger(self.data_dir / "ledger.jsonl")
        self.scheduler = ExperimentScheduler(self.ledger)
        self.subject_id = subject_id

    def record_decision_episode(
        self,
        *,
        situation: dict[str, Any],
        available_actions: list[str],
        pre_decision_context: dict[str, Any],
        observed_action: dict[str, Any] | None = None,
        later_outcomes: dict[str, Any] | None = None,
        data_provenance: dict[str, Any] | None = None,
        decision_time: str | None = None,
        observation_cutoff: str | None = None,
    ) -> DecisionEpisode:
        decision_time = decision_time or utc_now()
        episode = DecisionEpisode.create(
            subject_id=self.subject_id,
            decision_time=decision_time,
            observation_cutoff=observation_cutoff or decision_time,
            situation=situation,
            available_actions=available_actions,
            pre_decision_context=pre_decision_context,
            observed_action=observed_action,
            later_outcomes=later_outcomes,
            data_provenance=data_provenance or {"source": "manual"},
        )
        self.ledger.append("decision_episode", episode, record_id=episode.episode_id)
        return episode

    def preregister_task_start_experiment(
        self,
        *,
        treatment: str = "explicit_first_step",
        comparator: str = "generic_task_description",
        planned_trials: int = 24,
        population: str = "eligible planned tasks",
    ) -> str:
        return self.scheduler.preregister(
            question="Does the treatment cause faster task initiation than the comparator?",
            treatment=treatment,
            comparator=comparator,
            target="started_within_10_minutes",
            population=population,
            planned_trials=planned_trials,
            stopping_rule=f"Stop after {planned_trials} eligible randomized assignments; do not stop based on interim effect.",
            analysis_plan="Estimate randomized difference in means and report uncertainty interval by block.",
            approval_required=True,
        )

    def assign_for_task(
        self,
        context: dict[str, Any],
        *,
        treatment: str,
        comparator: str,
        preregistration_id: str,
        probability: float = 0.5,
    ) -> dict[str, Any]:
        return self.scheduler.assign_intervention(
            context,
            treatment=treatment,
            comparator=comparator,
            probability=probability,
            preregistration_id=preregistration_id,
        )

    def capture_trial_outcome(
        self,
        assignment: dict[str, Any],
        *,
        started_within_10_minutes: bool,
        time_to_start_seconds: int,
        completed_within_day: bool,
    ) -> dict[str, Any]:
        trial = self.scheduler.record_trial_outcome(
            assignment,
            {
                "started_within_10_minutes": started_within_10_minutes,
                "time_to_start_seconds": time_to_start_seconds,
                "completed_within_day": completed_within_day,
            },
            subject_id=self.subject_id,
        )
        return asdict(trial)

    def estimate_effect(
        self, treatment: str, comparator: str, *, preregistration_id: str | None = None
    ) -> dict[str, Any]:
        return self.scheduler.estimate_treatment_effect(
            treatment=treatment,
            comparator=comparator,
            outcome_name="started_within_10_minutes",
            preregistration_id=preregistration_id,
        )

    def freeze_model_for_prospective_block(self, model_id: str, reason: str) -> dict[str, Any]:
        """Reject artifact-free freezes and direct callers to the research lockbox.

        A scientifically meaningful freeze must bind a persisted model artifact,
        its training snapshot, and an immutable split manifest. ``PersonalLab``
        records decisions and experiments but does not fit or persist predictor
        artifacts, so fabricating a freeze marker here would create false
        prospective guarantees. Use ``ResearchAPI.freeze_candidate`` after the
        personal-data bridge has materialized a campaign and fitted the model.
        """

        del model_id, reason
        raise RuntimeError(
            "PersonalLab cannot freeze an unregistered model. Fit and persist the model "
            "through ResearchAPI, then call ResearchAPI.freeze_candidate so the freeze "
            "is bound to an artifact hash, training snapshot, split snapshot, and ledger cut."
        )

    def launch_real_intervention(self, proposal: ExperimentProposal, *, approved_by_human: bool = False) -> dict[str, Any]:
        return self.scheduler.launch_real_intervention(proposal, approved_by_human=approved_by_human)
