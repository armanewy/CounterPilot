from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
import math
from statistics import mean
from typing import Any

from behavior_lab.ledger import ImmutableLedger


@dataclass(frozen=True)
class TreatmentComparison:
    treatment: str
    comparator: str
    outcome: str
    treatment_n: int
    comparator_n: int
    treatment_mean: float
    comparator_mean: float
    difference_in_means: float
    standard_error: float
    uncertainty_interval: list[float]
    by_block: dict[str, dict[str, Any]]
    estimand: str = "intention_to_treat"
    nonadherent_trials: int = 0
    warning: str | None = None
    estimator: str = "unweighted_randomized_difference_in_means"
    assignment_probability_range: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TreatmentComparator:
    """Transparent intention-to-treat comparison for randomized binary outcomes.

    The estimator intentionally stays simple, but no longer reports a spuriously
    zero-width interval when a tiny arm has all successes or all failures. It uses
    the Agresti-Caffo add-two adjustment for uncertainty.
    """

    def __init__(self, ledger: ImmutableLedger):
        self.ledger = ledger

    def compare(
        self,
        *,
        treatment: str,
        comparator: str,
        outcome_name: str,
        preregistration_id: str | None = None,
    ) -> TreatmentComparison:
        if not treatment or not comparator or treatment == comparator:
            raise ValueError("treatment and comparator must be distinct non-empty values")
        treatment_values: list[float] = []
        comparator_values: list[float] = []
        treatment_probabilities: list[float] = []
        comparator_probabilities: list[float] = []
        all_probabilities: list[float] = []
        blocks: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(
            lambda: {"treatment": [], "comparator": []}
        )
        nonadherent = 0

        for trial in self.ledger.payloads("intervention_trial"):
            if preregistration_id and trial.get("preregistration_id") != preregistration_id:
                continue
            comparison = trial.get("comparison", {})
            if comparison.get("treatment") != treatment or comparison.get("comparator") != comparator:
                continue
            outcomes = trial.get("outcomes", {})
            if outcome_name not in outcomes:
                continue
            raw_value = outcomes[outcome_name]
            if not isinstance(raw_value, (bool, int)) or int(raw_value) not in {0, 1}:
                raise ValueError(f"Outcome {outcome_name!r} must be binary for TreatmentComparator")
            assignment = trial.get("assignment", {})
            assigned = assignment.get("assigned_treatment")
            if assigned not in {treatment, comparator}:
                raise ValueError("Trial assignment is inconsistent with its treatment comparison")
            treatment_probability = assignment.get("treatment_probability", assignment.get("probability"))
            if treatment_probability is None or not 0.0 < float(treatment_probability) < 1.0:
                raise ValueError("Trial is missing a valid randomized treatment probability")
            treatment_probability = float(treatment_probability)
            all_probabilities.append(treatment_probability)
            adherence = trial.get("adherence", {})
            if adherence and not (adherence.get("treatment_delivered", True) and adherence.get("treatment_seen", True)):
                nonadherent += 1
            value = float(int(raw_value))
            block = _block_key(assignment.get("block", {}))
            if assigned == treatment:
                treatment_values.append(value)
                treatment_probabilities.append(treatment_probability)
                blocks[block]["treatment"].append((value, treatment_probability))
            else:
                comparator_values.append(value)
                comparator_probabilities.append(treatment_probability)
                blocks[block]["comparator"].append((value, treatment_probability))

        varying_propensity = bool(all_probabilities) and (
            max(all_probabilities) - min(all_probabilities) > 1e-12
        )
        if varying_propensity:
            treatment_mean = _weighted_mean(
                treatment_values,
                [1.0 / probability for probability in treatment_probabilities],
            )
            comparator_mean = _weighted_mean(
                comparator_values,
                [1.0 / (1.0 - probability) for probability in comparator_probabilities],
            )
            se, interval = _weighted_difference_interval(
                treatment_values,
                treatment_probabilities,
                comparator_values,
                comparator_probabilities,
            )
            estimator = "hajek_inverse_probability_weighted"
        else:
            treatment_mean = mean(treatment_values) if treatment_values else 0.0
            comparator_mean = mean(comparator_values) if comparator_values else 0.0
            se, interval = _agresti_caffo_difference_interval(treatment_values, comparator_values)
            estimator = "unweighted_randomized_difference_in_means"
        effect = treatment_mean - comparator_mean

        warnings: list[str] = []
        if not treatment_values or not comparator_values:
            warnings.append("one randomized arm has no observations")
        elif len(treatment_values) < 10 or len(comparator_values) < 10:
            warnings.append("small randomized sample; treat the estimate as a smoke signal")
        if nonadherent:
            warnings.append(f"{nonadherent} trial(s) were nonadherent; estimate remains intention-to-treat")
        if varying_propensity:
            warnings.append("assignment probabilities varied; reporting a Hajek inverse-probability-weighted estimate")

        by_block: dict[str, dict[str, Any]] = {}
        for block, values in sorted(blocks.items()):
            t_pairs = values["treatment"]
            c_pairs = values["comparator"]
            t_vals = [item[0] for item in t_pairs]
            c_vals = [item[0] for item in c_pairs]
            t_probs = [item[1] for item in t_pairs]
            c_probs = [item[1] for item in c_pairs]
            block_varies = bool(t_probs or c_probs) and (
                max(t_probs + c_probs) - min(t_probs + c_probs) > 1e-12
            )
            if block_varies:
                t_mean = _weighted_mean(t_vals, [1.0 / probability for probability in t_probs])
                c_mean = _weighted_mean(c_vals, [1.0 / (1.0 - probability) for probability in c_probs])
                _, block_interval = _weighted_difference_interval(t_vals, t_probs, c_vals, c_probs)
            else:
                t_mean = mean(t_vals) if t_vals else 0.0
                c_mean = mean(c_vals) if c_vals else 0.0
                _, block_interval = _agresti_caffo_difference_interval(t_vals, c_vals)
            by_block[block] = {
                "treatment_n": len(t_vals),
                "comparator_n": len(c_vals),
                "treatment_mean": t_mean,
                "comparator_mean": c_mean,
                "difference_in_means": t_mean - c_mean,
                "uncertainty_interval": block_interval,
            }

        return TreatmentComparison(
            treatment=treatment,
            comparator=comparator,
            outcome=outcome_name,
            treatment_n=len(treatment_values),
            comparator_n=len(comparator_values),
            treatment_mean=treatment_mean,
            comparator_mean=comparator_mean,
            difference_in_means=effect,
            standard_error=se,
            uncertainty_interval=interval,
            by_block=by_block,
            nonadherent_trials=nonadherent,
            warning="; ".join(warnings) if warnings else None,
            estimator=estimator,
            assignment_probability_range=(
                [min(all_probabilities), max(all_probabilities)] if all_probabilities else []
            ),
        )


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) != len(weights):
        raise ValueError("values and weights must have equal length")
    total_weight = sum(weights)
    if total_weight <= 0.0 or not math.isfinite(total_weight):
        raise ValueError("inverse-probability weights are invalid")
    return sum(value * weight for value, weight in zip(values, weights, strict=True)) / total_weight


def _weighted_arm_variance(values: list[float], weights: list[float], estimate: float) -> float:
    if not values:
        return 0.0
    total_weight = sum(weights)
    if total_weight <= 0.0:
        return 0.0
    # Ratio-estimator sandwich approximation. It is intentionally transparent,
    # not a replacement for a full causal-inference package.
    return sum((weight**2) * ((value - estimate) ** 2) for value, weight in zip(values, weights, strict=True)) / (
        total_weight**2
    )


def _weighted_difference_interval(
    treatment_values: list[float],
    treatment_probabilities: list[float],
    comparator_values: list[float],
    comparator_probabilities: list[float],
) -> tuple[float, list[float]]:
    if not treatment_values or not comparator_values:
        return 0.0, [-1.0, 1.0]
    t_weights = [1.0 / probability for probability in treatment_probabilities]
    c_weights = [1.0 / (1.0 - probability) for probability in comparator_probabilities]
    t_mean = _weighted_mean(treatment_values, t_weights)
    c_mean = _weighted_mean(comparator_values, c_weights)
    effect = t_mean - c_mean
    variance = _weighted_arm_variance(treatment_values, t_weights, t_mean) + _weighted_arm_variance(
        comparator_values, c_weights, c_mean
    )
    se = math.sqrt(max(variance, 0.0))
    # If an arm is all successes/failures, the sandwich estimate can be zero.
    # Fall back to the conservative add-two width rather than claim certainty.
    if se <= 1e-12:
        se, conservative = _agresti_caffo_difference_interval(treatment_values, comparator_values)
        return se, conservative
    return se, [max(-1.0, effect - 1.96 * se), min(1.0, effect + 1.96 * se)]


def _agresti_caffo_difference_interval(
    treatment_values: list[float],
    comparator_values: list[float],
) -> tuple[float, list[float]]:
    if not treatment_values or not comparator_values:
        return 0.0, [-1.0, 1.0]
    # Add one success and one failure to each arm (Agresti-Caffo).
    t_n = len(treatment_values) + 2
    c_n = len(comparator_values) + 2
    t_rate = (sum(treatment_values) + 1.0) / t_n
    c_rate = (sum(comparator_values) + 1.0) / c_n
    adjusted_difference = t_rate - c_rate
    se = math.sqrt(t_rate * (1.0 - t_rate) / t_n + c_rate * (1.0 - c_rate) / c_n)
    return se, [max(-1.0, adjusted_difference - 1.96 * se), min(1.0, adjusted_difference + 1.96 * se)]


def _block_key(block: dict[str, Any]) -> str:
    if not block:
        return "unblocked"
    return "|".join(f"{key}={block[key]}" for key in sorted(block))
