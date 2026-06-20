from __future__ import annotations

from dataclasses import dataclass
import random
from statistics import mean
from typing import Any

from behavior_lab.core import InterventionTrial, new_id, parse_time, stable_hash, utc_now
from behavior_lab.causal import TreatmentComparator
from behavior_lab.evaluation import BinaryPredictor
from behavior_lab.ledger import DuplicateRecordError, ImmutableLedger


class ExperimentIntegrityError(RuntimeError):
    pass


class ExperimentLimitError(ExperimentIntegrityError):
    pass


class DuplicateTrialOutcomeError(ExperimentIntegrityError):
    pass


@dataclass(frozen=True)
class ExperimentProposal:
    experiment_id: str
    mode: str
    context: dict[str, Any]
    treatment: str
    comparator: str
    expected_hypothesis_separation: float
    cost: float
    risk: float
    participant_burden: float
    utility: float
    model_predictions: dict[str, dict[str, float]]


class ExperimentScheduler:
    def __init__(self, ledger: ImmutableLedger, seed: int = 11):
        self.ledger = ledger
        self.random_seed = int(seed)
        # Kept for compatibility with callers that inspect this attribute.  The
        # actual assignment draw is keyed by assignment ID so restarts do not
        # rewind a pseudo-random stream.
        self.random = random.Random(seed)

    def preregister(
        self,
        *,
        question: str,
        treatment: str,
        comparator: str,
        target: str,
        population: str,
        planned_trials: int,
        stopping_rule: str,
        analysis_plan: str,
        approval_required: bool = True,
    ) -> str:
        if not question.strip() or not target.strip() or not population.strip():
            raise ValueError("question, target, and population must be non-empty")
        if not treatment.strip() or not comparator.strip() or treatment == comparator:
            raise ValueError("treatment and comparator must be distinct non-empty values")
        if planned_trials <= 0:
            raise ValueError("planned_trials must be positive")
        if not stopping_rule.strip() or not analysis_plan.strip():
            raise ValueError("stopping_rule and analysis_plan must be specified before assignment")
        # Give repeated registrations of the same scientific design distinct,
        # deterministic randomization sequences. The first occurrence is sequence
        # zero in every clean run, preserving seed reproducibility; later repeated
        # experiments receive independent allocation streams without relying on a
        # random record identifier.
        preregistration_id = new_id("pre")
        created_at = utc_now()
        for _ in range(5):
            records = self.ledger.scan()
            draft = {
                "preregistration_id": preregistration_id,
                "question": question,
                "comparison": {"treatment": treatment, "comparator": comparator},
                "target": target,
                "population": population,
                "planned_trials": planned_trials,
                "stopping_rule": stopping_rule,
                "analysis_plan": analysis_plan,
                "approval_required": approval_required,
                "created_at": created_at,
            }
            design_signature = _preregistration_signature(draft)
            design_sequence = sum(
                1
                for record in records
                if record.get("record_type") == "experiment_preregistration"
                and _preregistration_signature(record.get("payload", {})) == design_signature
            )
            payload = {
                **draft,
                "experiment_signature": design_signature,
                "randomization_sequence": design_sequence,
            }

            def guard(current_records: list[dict[str, Any]]) -> None:
                current_sequence = sum(
                    1
                    for record in current_records
                    if record.get("record_type") == "experiment_preregistration"
                    and _preregistration_signature(record.get("payload", {}))
                    == design_signature
                )
                if current_sequence != design_sequence:
                    raise ExperimentIntegrityError(
                        "Concurrent preregistration changed the randomization sequence; retry"
                    )

            try:
                self.ledger.append_guarded(
                    "experiment_preregistration",
                    payload,
                    record_id=preregistration_id,
                    unique_record_id=True,
                    guard=guard,
                )
                return preregistration_id
            except DuplicateRecordError:
                preregistration_id = new_id("pre")
            except ExperimentIntegrityError as exc:
                if "retry" not in str(exc):
                    raise
        raise ExperimentIntegrityError("Could not reserve a preregistration sequence")

    def assign_intervention(
        self,
        context: dict[str, Any],
        *,
        treatment: str,
        comparator: str,
        probability: float = 0.5,
        preregistration_id: str | None = None,
        assigned_at: str | None = None,
    ) -> dict[str, Any]:
        if not 0.0 < probability < 1.0:
            raise ValueError("Assignment probability must be strictly between 0 and 1")
        if treatment == comparator:
            raise ValueError("Treatment and comparator must differ")

        # The allocation is a deterministic function of the experiment
        # specification and sequential assignment index. This preserves the
        # randomization distribution while making clean reruns and process
        # restarts reproducible. A guarded append detects concurrent writers and
        # retries with the next authoritative index.
        for _ in range(5):
            records = self.ledger.scan()
            prereg = (
                _find_preregistration(records, preregistration_id)
                if preregistration_id is not None
                else None
            )
            if preregistration_id is not None and prereg is None:
                raise ExperimentIntegrityError(f"Unknown preregistration: {preregistration_id}")
            if prereg is not None:
                expected = prereg.get("comparison", {})
                if expected.get("treatment") != treatment or expected.get("comparator") != comparator:
                    raise ExperimentIntegrityError("Assignment comparison does not match preregistration")
                relevant = [
                    record
                    for record in records
                    if record.get("record_type") == "intervention_assignment"
                    and record.get("payload", {}).get("preregistration_id") == preregistration_id
                ]
                experiment_signature = _preregistration_signature(prereg)
            else:
                relevant = [
                    record
                    for record in records
                    if record.get("record_type") == "intervention_assignment"
                    and record.get("payload", {}).get("preregistration_id") is None
                    and record.get("payload", {}).get("comparison")
                    == {"treatment": treatment, "comparator": comparator}
                ]
                experiment_signature = stable_hash(
                    {"treatment": treatment, "comparator": comparator, "unpreregistered": True}
                )
            assignment_index = len(relevant)
            # Identical scientific designs remain comparable through the design
            # signature, but each preregistration receives its own persisted
            # randomization namespace. Otherwise two independent repetitions of
            # the same experiment would receive the exact same allocation
            # sequence and could correlate assignment with context order.
            randomization_namespace = stable_hash(
                {
                    "experiment_signature": experiment_signature,
                    "randomization_sequence": (
                        int(prereg.get("randomization_sequence", 0))
                        if prereg is not None
                        else 0
                    ),
                    "unpreregistered": prereg is None,
                }
            )
            assignment_id = f"a_{stable_hash({
                'randomization_namespace': randomization_namespace,
                'index': assignment_index,
            })[:12]}"
            draw = int(
                stable_hash(
                    {
                        "seed": self.random_seed,
                        "randomization_namespace": randomization_namespace,
                        "assignment_index": assignment_index,
                    }
                )[:16],
                16,
            ) / float(16**16)
            assigned = treatment if draw < probability else comparator
            assignment_time = assigned_at or utc_now()
            parse_time(assignment_time)
            assignment = {
                "assignment_id": assignment_id,
                "assignment_index": assignment_index,
                "context_snapshot": dict(context),
                "comparison": {"treatment": treatment, "comparator": comparator},
                "assignment": {
                    "method": "randomized_block",
                    "assigned_treatment": assigned,
                    "probability": probability,
                    "treatment_probability": probability,
                    "assigned_probability": probability if assigned == treatment else 1.0 - probability,
                    "block": self._block(context),
                },
                "preregistration_id": preregistration_id,
                "assigned_at": assignment_time,
                "randomization": {
                    "seed": self.random_seed,
                    "experiment_signature": experiment_signature,
                    "randomization_namespace": randomization_namespace,
                    "assignment_index": assignment_index,
                    "draw": draw,
                },
            }

            def guard(current_records: list[dict[str, Any]]) -> None:
                current_prereg = (
                    _find_preregistration(current_records, preregistration_id)
                    if preregistration_id is not None
                    else None
                )
                if preregistration_id is not None:
                    if current_prereg is None:
                        raise ExperimentIntegrityError(
                            f"Unknown preregistration: {preregistration_id}"
                        )
                    expected = current_prereg.get("comparison", {})
                    if expected.get("treatment") != treatment or expected.get("comparator") != comparator:
                        raise ExperimentIntegrityError(
                            "Assignment comparison does not match preregistration"
                        )
                    current_relevant = [
                        record
                        for record in current_records
                        if record.get("record_type") == "intervention_assignment"
                        and record.get("payload", {}).get("preregistration_id")
                        == preregistration_id
                    ]
                    if len(current_relevant) >= int(current_prereg.get("planned_trials", 0)):
                        raise ExperimentLimitError(
                            f"Preregistered assignment limit reached for {preregistration_id}: "
                            f"{len(current_relevant)}"
                        )
                else:
                    current_relevant = [
                        record
                        for record in current_records
                        if record.get("record_type") == "intervention_assignment"
                        and record.get("payload", {}).get("preregistration_id") is None
                        and record.get("payload", {}).get("comparison")
                        == {"treatment": treatment, "comparator": comparator}
                    ]
                if len(current_relevant) != assignment_index:
                    raise ExperimentIntegrityError(
                        "Concurrent assignment changed the randomization index; retry"
                    )
                if any(
                    record.get("payload", {}).get("assignment_index") == assignment_index
                    for record in current_relevant
                ):
                    raise ExperimentIntegrityError(
                        "Randomization index is already present; retry"
                    )

            try:
                self.ledger.append_guarded(
                    "intervention_assignment",
                    assignment,
                    record_id=assignment_id,
                    unique_record_id=True,
                    guard=guard,
                )
                return assignment
            except DuplicateRecordError:
                continue
            except ExperimentIntegrityError as exc:
                if "retry" not in str(exc):
                    raise
        raise ExperimentIntegrityError("Could not reserve a stable randomization index")

    def record_trial_outcome(
        self,
        assignment: dict[str, Any],
        outcomes: dict[str, Any],
        *,
        adherence: dict[str, Any] | None = None,
        measurement_horizons: list[str] | None = None,
        subject_id: str = "arman",
        recorded_at: str | None = None,
        data_provenance: dict[str, Any] | None = None,
    ) -> InterventionTrial:
        assignment_id = str(assignment.get("assignment_id", ""))
        if not assignment_id:
            raise ExperimentIntegrityError("assignment_id is required")
        intervened_context = _apply_intervention(
            assignment["context_snapshot"], assignment["assignment"]["assigned_treatment"]
        )
        # Adapter metadata may add provenance, but it may not overwrite the
        # canonical randomized context used for analysis.
        provenance = dict(data_provenance or {})
        provenance.update(
            {
                "context_snapshot": assignment["context_snapshot"],
                "intervened_context": intervened_context,
                "manual_or_adapter_capture": True,
            }
        )
        trial = InterventionTrial.create(
            subject_id=subject_id,
            context_snapshot_id=assignment_id,
            comparison=assignment["comparison"],
            assignment=assignment["assignment"],
            adherence=adherence or {"treatment_delivered": True, "treatment_seen": True},
            outcomes=outcomes,
            measurement_horizons=measurement_horizons or ["10_minutes", "2_hours", "1_day"],
            preregistration_id=assignment.get("preregistration_id"),
            recorded_at=recorded_at,
            data_provenance=provenance,
        )

        def guard(records: list[dict[str, Any]]) -> None:
            matching_assignment = next(
                (
                    record.get("payload")
                    for record in records
                    if record.get("record_type") == "intervention_assignment"
                    and record.get("record_id") == assignment_id
                ),
                None,
            )
            if matching_assignment is None:
                raise ExperimentIntegrityError("Outcome references an assignment not present in this ledger")
            for key in (
                "assignment_index",
                "context_snapshot",
                "comparison",
                "assignment",
                "preregistration_id",
                "assigned_at",
                "randomization",
            ):
                if matching_assignment.get(key) != assignment.get(key):
                    raise ExperimentIntegrityError(
                        f"Outcome assignment field {key!r} does not match the immutable ledger assignment"
                    )
            if parse_time(trial.recorded_at) < parse_time(str(matching_assignment.get("assigned_at"))):
                raise ExperimentIntegrityError("Outcome timestamp occurs before randomized assignment")
            preregistration_id = matching_assignment.get("preregistration_id")
            if preregistration_id:
                prereg = _find_preregistration(records, str(preregistration_id))
                if prereg is None:
                    raise ExperimentIntegrityError("Outcome references a missing preregistration")
                target = prereg.get("target")
                if target and target not in outcomes:
                    raise ExperimentIntegrityError(
                        f"Outcome is missing preregistered target {target!r}"
                    )
            duplicate = any(
                record.get("record_type") == "intervention_trial"
                and record.get("payload", {}).get("context_snapshot_id") == assignment_id
                for record in records
            )
            if duplicate:
                raise DuplicateTrialOutcomeError(f"Outcome already exists for assignment {assignment_id}")

        try:
            self.ledger.append_guarded(
                "intervention_trial",
                trial,
                record_id=trial.trial_id,
                unique_record_id=True,
                guard=guard,
            )
        except DuplicateRecordError as exc:
            raise DuplicateTrialOutcomeError(str(exc)) from exc
        return trial

    def estimate_treatment_effect(
        self,
        *,
        treatment: str,
        comparator: str,
        outcome_name: str,
        preregistration_id: str | None = None,
    ) -> dict[str, Any]:
        return TreatmentComparator(self.ledger).compare(
            treatment=treatment,
            comparator=comparator,
            outcome_name=outcome_name,
            preregistration_id=preregistration_id,
        ).to_dict()

    def launch_real_intervention(
        self,
        proposal: ExperimentProposal,
        *,
        approved_by_human: bool = False,
        preregistration_id: str | None = None,
    ) -> dict[str, Any]:
        if not approved_by_human:
            raise PermissionError("Real interventions require explicit human approval.")
        assignment = self.assign_intervention(
            proposal.context,
            treatment=proposal.treatment,
            comparator=proposal.comparator,
            probability=0.5,
            preregistration_id=preregistration_id,
        )
        self.ledger.append(
            "real_intervention_launch",
            {"proposal": proposal.__dict__, "assignment_id": assignment["assignment_id"], "launched_at": utc_now()},
        )
        return assignment

    def _block(self, context: dict[str, Any]) -> dict[str, str]:
        fatigue = float(context.get("fatigue", 0.0))
        return {
            "time_of_day": "morning" if context.get("time_of_day_morning", 0.0) else "not_morning",
            "fatigue_band": "high" if fatigue > 0.66 else "medium" if fatigue > 0.33 else "low",
            "task_size": "large" if context.get("task_size_large", 0.0) else "small_or_medium",
        }


class DisagreementFinder:
    def propose(
        self,
        models: list[BinaryPredictor],
        candidate_contexts: list[dict[str, Any]],
        *,
        treatment: str = "explicit_first_step",
        comparator: str = "generic_task_description",
        mode: str = "science",
        lambda_cost: float = 0.1,
        lambda_risk: float = 0.1,
        lambda_burden: float = 0.1,
    ) -> ExperimentProposal:
        if not models:
            raise ValueError("At least one model is required")
        if treatment == comparator:
            raise ValueError("Treatment and comparator must differ")
        if mode not in {"science", "optimization"}:
            raise ValueError("mode must be 'science' or 'optimization'")
        best: ExperimentProposal | None = None
        for context in candidate_contexts:
            treatment_context = _apply_intervention(context, treatment)
            comparator_context = _apply_intervention(context, comparator)
            predictions: dict[str, dict[str, float]] = {}
            treatment_values = []
            comparator_values = []
            for model in models:
                pt = model.predict_proba(treatment_context)
                pc = model.predict_proba(comparator_context)
                predictions[model.model_id] = {"treatment": pt, "comparator": pc, "effect": pt - pc}
                treatment_values.append(pt)
                comparator_values.append(pc)
            if mode == "science":
                effects = [value["effect"] for value in predictions.values()]
                separation = max(
                    max(treatment_values) - min(treatment_values),
                    max(comparator_values) - min(comparator_values),
                    max(effects) - min(effects),
                )
            else:
                separation = mean(value["effect"] for value in predictions.values())
            cost = 0.2 if context.get("task_size_large", 0.0) else 0.1
            risk = 0.05
            burden = 0.15 + 0.1 * float(context.get("fatigue", 0.0))
            utility = separation - lambda_cost * cost - lambda_risk * risk - lambda_burden * burden
            proposal = ExperimentProposal(
                experiment_id=new_id("x"),
                mode=mode,
                context=dict(context),
                treatment=treatment,
                comparator=comparator,
                expected_hypothesis_separation=separation,
                cost=cost,
                risk=risk,
                participant_burden=burden,
                utility=utility,
                model_predictions=predictions,
            )
            if best is None or proposal.utility > best.utility:
                best = proposal
        if best is None:
            raise ValueError("No candidate contexts available for experiment proposal")
        return best


def _preregistration_signature(preregistration: dict[str, Any]) -> str:
    """Hash only the scientific design, excluding random IDs/timestamps."""

    return stable_hash(
        {
            "question": preregistration.get("question"),
            "comparison": preregistration.get("comparison"),
            "target": preregistration.get("target"),
            "population": preregistration.get("population"),
            "planned_trials": preregistration.get("planned_trials"),
            "stopping_rule": preregistration.get("stopping_rule"),
            "analysis_plan": preregistration.get("analysis_plan"),
            "approval_required": preregistration.get("approval_required"),
        }
    )


def _find_preregistration(records: list[dict[str, Any]], preregistration_id: str) -> dict[str, Any] | None:
    match = None
    for record in records:
        if record.get("record_type") != "experiment_preregistration":
            continue
        payload = record.get("payload", {})
        if payload.get("preregistration_id") == preregistration_id:
            match = payload
    return match


def _apply_intervention(context: dict[str, Any], intervention: str) -> dict[str, Any]:
    updated = dict(context)
    if intervention == "explicit_first_step":
        updated["explicit_first_step"] = 1.0
    elif intervention in {"generic_task_description", "no_intervention"}:
        if intervention == "generic_task_description":
            updated["explicit_first_step"] = 0.0
    elif intervention == "visible_commitment":
        updated["public_commitment"] = 1.0
    elif intervention == "two_minute_countdown":
        updated["deadline_near"] = 1.0
    return updated
