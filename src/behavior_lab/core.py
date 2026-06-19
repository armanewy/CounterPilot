from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
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
    return value


def stable_json(value: Any) -> str:
    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def parse_time(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    return datetime.fromisoformat(text)


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
    preregistration_id: str | None = None
    data_provenance: JsonDict = field(default_factory=dict)

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
    ) -> "HypothesisSpec":
        return cls(
            hypothesis_id=hypothesis_id,
            target={"name": target_name, "type": "binary"},
            validity={"subject": subject, "domains": ["task_initiation"], "trained_through": None},
            structure={"family": "logistic_formula", "terms": terms},
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
