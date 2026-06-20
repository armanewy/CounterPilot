from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any
from uuid import uuid4


JsonDict = dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, set):
        converted = [to_jsonable(item) for item in value]
        return sorted(converted, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite floats are not valid ledger values")
    return value


def stable_json(value: Any) -> str:
    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def parse_time(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Timestamp must be a non-empty ISO-8601 string")
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Timestamp must be timezone-aware: {value!r}")
    return parsed


def _require_nonempty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")


@dataclass(frozen=True)
class DecisionEpisode:
    episode_id: str
    subject_id: str
    decision_time: str
    observation_cutoff: str
    situation: JsonDict
    available_actions: list[str]
    pre_decision_context: JsonDict
    observed_action: JsonDict | None = None
    later_outcomes: JsonDict | None = None
    data_provenance: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonempty(self.episode_id, "episode_id")
        _require_nonempty(self.subject_id, "subject_id")
        decision = parse_time(self.decision_time)
        cutoff = parse_time(self.observation_cutoff)
        if cutoff > decision:
            raise ValueError("observation_cutoff may not occur after decision_time")
        if not self.available_actions or any(not str(action).strip() for action in self.available_actions):
            raise ValueError("available_actions must contain at least one non-empty action")
        if len(set(self.available_actions)) != len(self.available_actions):
            raise ValueError("available_actions must be unique")
        if self.observed_action is not None:
            action = self.observed_action.get("action")
            if action is not None and action not in self.available_actions:
                raise ValueError("observed action must appear in available_actions")

    @classmethod
    def create(
        cls,
        subject_id: str,
        decision_time: str,
        situation: JsonDict,
        available_actions: list[str],
        pre_decision_context: JsonDict,
        observation_cutoff: str | None = None,
        observed_action: JsonDict | None = None,
        later_outcomes: JsonDict | None = None,
        data_provenance: JsonDict | None = None,
    ) -> "DecisionEpisode":
        return cls(
            episode_id=new_id("e"),
            subject_id=subject_id,
            decision_time=decision_time,
            observation_cutoff=observation_cutoff or decision_time,
            situation=situation,
            available_actions=available_actions,
            pre_decision_context=pre_decision_context,
            observed_action=observed_action,
            later_outcomes=later_outcomes,
            data_provenance=data_provenance or {},
        )


@dataclass(frozen=True)
class InterventionTrial:
    trial_id: str
    subject_id: str
    context_snapshot_id: str
    comparison: JsonDict
    assignment: JsonDict
    adherence: JsonDict
    outcomes: JsonDict
    measurement_horizons: list[str]
    recorded_at: str = field(default_factory=utc_now)
    preregistration_id: str | None = None
    data_provenance: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonempty(self.trial_id, "trial_id")
        _require_nonempty(self.subject_id, "subject_id")
        _require_nonempty(self.context_snapshot_id, "context_snapshot_id")
        parse_time(self.recorded_at)
        treatment = self.comparison.get("treatment")
        comparator = self.comparison.get("comparator")
        if not treatment or not comparator or treatment == comparator:
            raise ValueError("comparison must contain distinct treatment and comparator values")
        assigned = self.assignment.get("assigned_treatment")
        if assigned not in {treatment, comparator}:
            raise ValueError("assigned_treatment must be treatment or comparator")
        probability = self.assignment.get("treatment_probability", self.assignment.get("probability"))
        if probability is None or not 0.0 < float(probability) < 1.0:
            raise ValueError("assignment must contain a treatment probability strictly between 0 and 1")
        treatment_probability = float(probability)
        expected_assigned_probability = (
            treatment_probability if assigned == treatment else 1.0 - treatment_probability
        )
        assigned_probability = self.assignment.get("assigned_probability", expected_assigned_probability)
        if not math.isclose(
            float(assigned_probability), expected_assigned_probability, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ValueError("assigned_probability is inconsistent with the randomized assignment")
        if not self.measurement_horizons:
            raise ValueError("measurement_horizons may not be empty")

    @classmethod
    def create(
        cls,
        subject_id: str,
        context_snapshot_id: str,
        comparison: JsonDict,
        assignment: JsonDict,
        adherence: JsonDict,
        outcomes: JsonDict,
        measurement_horizons: list[str],
        preregistration_id: str | None = None,
        data_provenance: JsonDict | None = None,
        recorded_at: str | None = None,
    ) -> "InterventionTrial":
        return cls(
            trial_id=new_id("t"),
            subject_id=subject_id,
            context_snapshot_id=context_snapshot_id,
            comparison=comparison,
            assignment=assignment,
            adherence=adherence,
            outcomes=outcomes,
            measurement_horizons=measurement_horizons,
            recorded_at=recorded_at or utc_now(),
            preregistration_id=preregistration_id,
            data_provenance=data_provenance or {},
        )


@dataclass(frozen=True)
class HypothesisSpec:
    hypothesis_id: str
    target: JsonDict
    validity: JsonDict
    structure: JsonDict
    complexity: JsonDict
    assumptions: list[str]
    falsification_conditions: list[str]
    parent_ids: list[str] = field(default_factory=list)
    status: str = "candidate"

    def __post_init__(self) -> None:
        _require_nonempty(self.hypothesis_id, "hypothesis_id")
        if not self.target.get("name"):
            raise ValueError("hypothesis target name is required")
        if not self.falsification_conditions:
            raise ValueError("every hypothesis needs at least one falsification condition")

    @property
    def family(self) -> str:
        return str(self.structure.get("family", "unknown"))

    @classmethod
    def formula(
        cls,
        hypothesis_id: str,
        target_name: str,
        terms: list[str],
        subject: str = "synthetic",
        parent_ids: list[str] | None = None,
        assumptions: list[str] | None = None,
        falsification_conditions: list[str] | None = None,
        origin: str = "submitted",
    ) -> "HypothesisSpec":
        cleaned_terms = [str(term).strip() for term in terms if str(term).strip()]
        return cls(
            hypothesis_id=hypothesis_id,
            target={"name": target_name, "type": "binary"},
            validity={"subject": subject, "domains": ["task_initiation"], "trained_through": None},
            structure={"family": "logistic_formula", "terms": cleaned_terms, "origin": origin},
            complexity={"variables": 0, "operators": 0, "latent_states": 0},
            assumptions=assumptions or ["pre-decision variables are measured before outcome"],
            falsification_conditions=falsification_conditions
            or ["fails to improve prospective log loss over the base-rate model"],
            parent_ids=parent_ids or [],
        )


@dataclass(frozen=True)
class FittedHypothesisRecord:
    model_id: str
    hypothesis_id: str
    fitted_at: str
    training_split: str
    training_cases: int
    parameters: JsonDict
    artifact: JsonDict
    campaign_id: str = "default"


@dataclass(frozen=True)
class EvaluationMetrics:
    model_id: str
    split: str
    n: int
    log_loss: float
    brier_score: float
    calibration_error: float
    base_rate: float
    lift_over_base_log_loss: float
    complexity: int
    details: JsonDict = field(default_factory=dict)
