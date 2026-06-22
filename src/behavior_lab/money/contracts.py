from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from behavior_lab.core import parse_time, stable_hash, to_jsonable


DOMAIN_VALUES = {"seller", "event_market", "etf_risk", "procurement"}
ACTION_MODES = {"reactive", "interventional"}
EVIDENCE_STATES = {
    "proposed",
    "historically_evaluated",
    "blind_passed",
    "prospectively_incubating",
    "prospectively_verified",
    "paper_decision",
    "resolved_paper",
    "manually_approved_real",
    "resolved_real",
    "rejected",
    "expired",
}


class ContractValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Action:
    action_id: str
    action_type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    capital_required: float = 0.0
    maximum_possible_loss: float = 0.0
    fixed_costs: float = 0.0
    variable_costs: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    action_mode: str = "reactive"
    reversible: bool = False

    def __post_init__(self) -> None:
        _require_nonempty(self.action_id, "action_id")
        _require_nonempty(self.action_type, "action_type")
        if self.action_mode not in ACTION_MODES:
            raise ContractValidationError(f"action_mode must be one of {sorted(ACTION_MODES)}")
        for field_name in ("capital_required", "maximum_possible_loss", "fixed_costs"):
            value = float(getattr(self, field_name))
            if value < 0:
                raise ContractValidationError(f"{field_name} may not be negative")

    @property
    def is_reactive(self) -> bool:
        return self.action_mode == "reactive"

    @property
    def is_interventional(self) -> bool:
        return self.action_mode == "interventional"

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def action_hash(self) -> str:
        return stable_hash(self.to_dict())


@dataclass(frozen=True)
class FinancialDecisionContract:
    contract_id: str
    domain: str
    target: dict[str, Any]
    decision_horizon: str
    decision_deadline: str
    available_actions: list[Action]
    no_action_id: str
    payoff_specification: dict[str, Any]
    cost_policy: dict[str, Any]
    risk_policy: dict[str, Any]
    liquidity_policy: dict[str, Any]
    resolution_source: dict[str, Any]
    data_cutoff_policy: dict[str, Any]
    prospective_requirement: dict[str, Any]
    notification_threshold: dict[str, Any]
    paper_only: bool
    contract_version: str

    def __post_init__(self) -> None:
        _require_nonempty(self.contract_id, "contract_id")
        _require_nonempty(self.decision_horizon, "decision_horizon")
        _require_nonempty(self.contract_version, "contract_version")
        if self.domain not in DOMAIN_VALUES:
            raise ContractValidationError(f"domain must be one of {sorted(DOMAIN_VALUES)}")
        parse_time(self.decision_deadline)
        if not self.available_actions:
            raise ContractValidationError("available_actions may not be empty")
        action_ids = [action.action_id for action in self.available_actions]
        if len(set(action_ids)) != len(action_ids):
            raise ContractValidationError("available action IDs must be unique")
        if self.no_action_id not in set(action_ids):
            raise ContractValidationError("no_action_id must refer to an available action")
        if not isinstance(self.target, dict) or not self.target:
            raise ContractValidationError("target must be a non-empty object")
        if not self.paper_only:
            raise ContractValidationError("new financial contracts are paper_only in this wave")

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def contract_hash(self) -> str:
        return stable_hash(self.to_dict())

    def automatic_evaluation_actions(self) -> list[Action]:
        """Return actions eligible for deterministic automatic evaluation.

        Interventional actions may be recorded as alternatives, but this wave
        never makes them recommendation-eligible.
        """

        return [action for action in self.available_actions if action.is_reactive]

    def assert_action_eligible_for_automatic_evaluation(self, action_id: str) -> None:
        eligible = {action.action_id for action in self.automatic_evaluation_actions()}
        if action_id not in eligible:
            raise ContractValidationError(
                f"action {action_id!r} is not eligible for automatic evaluation in this wave"
            )


def _require_nonempty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} must be a non-empty string")
