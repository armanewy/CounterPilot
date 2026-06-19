from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, asdict
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
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TreatmentComparator:
    """Compare one intervention against another from preregistered randomized trials.

    This is intentionally simple. It is not a replacement for a causal inference
    library. Its job in the MVP is to keep the scientific bookkeeping honest:
    only logged intervention_trial records are considered, assignment is read
    from the randomized assignment record, and uncertainty is shown even when
    sample sizes are tiny.
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
        treatment_values: list[float] = []
        comparator_values: list[float] = []
        blocks: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"treatment": [], "comparator": []})

        for trial in self.ledger.payloads("intervention_trial"):
            if preregistration_id and trial.get("preregistration_id") != preregistration_id:
                continue
            comparison = trial.get("comparison", {})
            if comparison.get("treatment") != treatment or comparison.get("comparator") != comparator:
                continue
            outcomes = trial.get("outcomes", {})
            if outcome_name not in outcomes:
                continue
            assigned = trial.get("assignment", {}).get("assigned_treatment")
            value = 1.0 if outcomes[outcome_name] else 0.0
            block = _block_key(trial.get("assignment", {}).get("block", {}))
            if assigned == treatment:
                treatment_values.append(value)
                blocks[block]["treatment"].append(value)
            elif assigned == comparator:
                comparator_values.append(value)
                blocks[block]["comparator"].append(value)

        treatment_mean = mean(treatment_values) if treatment_values else 0.0
        comparator_mean = mean(comparator_values) if comparator_values else 0.0
        effect = treatment_mean - comparator_mean
        se = _difference_in_proportions_se(treatment_mean, len(treatment_values), comparator_mean, len(comparator_values))
        warning = None
        if len(treatment_values) < 5 or len(comparator_values) < 5:
            warning = "very small randomized sample; use only as a smoke signal"

        by_block: dict[str, dict[str, Any]] = {}
        for block, values in sorted(blocks.items()):
            t_vals = values["treatment"]
            c_vals = values["comparator"]
            t_mean = mean(t_vals) if t_vals else 0.0
            c_mean = mean(c_vals) if c_vals else 0.0
            by_block[block] = {
                "treatment_n": len(t_vals),
                "comparator_n": len(c_vals),
                "treatment_mean": t_mean,
                "comparator_mean": c_mean,
                "difference_in_means": t_mean - c_mean,
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
            uncertainty_interval=[effect - 1.96 * se, effect + 1.96 * se],
            by_block=by_block,
            warning=warning,
        )


def _difference_in_proportions_se(p1: float, n1: int, p0: float, n0: int) -> float:
    if n1 <= 0 or n0 <= 0:
        return 0.0
    var = max(p1 * (1.0 - p1), 1e-9) / n1 + max(p0 * (1.0 - p0), 1e-9) / n0
    return math.sqrt(var)


def _block_key(block: dict[str, Any]) -> str:
    if not block:
        return "unblocked"
    return "|".join(f"{key}={block[key]}" for key in sorted(block))
