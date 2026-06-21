from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations
import math
import random
from typing import Any

from behavior_lab.data_sources.registry import default_registry


@dataclass(frozen=True)
class UpliftReport:
    source_id: str
    evidence_role: str
    production_export_allowed: bool
    rows: int
    treatment_count: int
    control_count: int
    treatment_rate: float
    control_outcome_rate: float
    treatment_outcome_rate: float
    average_treatment_effect: float
    standard_error: float
    confidence_interval: tuple[float, float]
    negative_control_ate: float
    negative_control_passed: bool
    negative_control_method: str
    negative_control_interpretation: str
    permutation_p_value: float
    permutation_samples: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_randomized_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("Criteo uplift benchmark requires rows")
    treatments = [int(row["treatment"]) for row in rows]
    if not set(treatments) <= {0, 1}:
        raise ValueError("treatment must be binary")
    if len(set(treatments)) != 2:
        raise ValueError("both treatment and control rows are required")
    return {"rows": len(rows), "treatment_rate": sum(treatments) / len(treatments)}


def simple_uplift_report(rows: list[dict[str, Any]], *, outcome_key: str = "conversion") -> dict[str, Any]:
    validation = validate_randomized_rows(rows)
    treatment = [float(row[outcome_key]) for row in rows if int(row["treatment"]) == 1]
    control = [float(row[outcome_key]) for row in rows if int(row["treatment"]) == 0]
    treatment_rate = sum(treatment) / len(treatment)
    control_rate = sum(control) / len(control)
    ate = treatment_rate - control_rate
    standard_error = _difference_in_means_se(treatment, control)
    null_ates, permutation_method = _permutation_null_ates([float(row[outcome_key]) for row in rows], treated_count=len(treatment))
    negative_ate = sum(null_ates) / len(null_ates)
    p_value = _two_sided_permutation_p_value(null_ates, observed=ate)
    permission = default_registry().check("criteo_uplift", "production_export")
    return UpliftReport(
        source_id="criteo_uplift",
        evidence_role="CAUSAL_VALIDATION",
        production_export_allowed=permission.allowed,
        rows=len(rows),
        treatment_count=len(treatment),
        control_count=len(control),
        treatment_rate=float(validation["treatment_rate"]),
        control_outcome_rate=control_rate,
        treatment_outcome_rate=treatment_rate,
        average_treatment_effect=ate,
        standard_error=standard_error,
        confidence_interval=(ate - 1.96 * standard_error, ate + 1.96 * standard_error),
        negative_control_ate=negative_ate,
        negative_control_passed=abs(negative_ate) <= max(1e-12, standard_error),
        negative_control_method=permutation_method,
        negative_control_interpretation="sanity check for the permutation-null generator; not evidence of causal lift",
        permutation_p_value=p_value,
        permutation_samples=len(null_ates),
    ).to_dict()


def _difference_in_means_se(treatment: list[float], control: list[float]) -> float:
    return math.sqrt(_sample_variance(treatment) / len(treatment) + _sample_variance(control) / len(control))


def _sample_variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / (len(values) - 1)


def _permutation_null_ates(outcomes: list[float], *, treated_count: int, max_permutations: int = 2000) -> tuple[list[float], str]:
    indexes = range(len(outcomes))
    total_assignments = math.comb(len(outcomes), treated_count)
    if total_assignments > max_permutations:
        rng = random.Random(1729)
        null = []
        all_indexes = list(indexes)
        for _ in range(max_permutations):
            treated = set(rng.sample(all_indexes, treated_count))
            treatment_values = [outcomes[index] for index in all_indexes if index in treated]
            control_values = [outcomes[index] for index in all_indexes if index not in treated]
            null.append(sum(treatment_values) / len(treatment_values) - sum(control_values) / len(control_values))
        return null or [0.0], "sampled_permutation_null"
    null = []
    for treated_indexes in combinations(indexes, treated_count):
        treated = set(treated_indexes)
        treatment_values = [outcomes[index] for index in indexes if index in treated]
        control_values = [outcomes[index] for index in indexes if index not in treated]
        null.append(sum(treatment_values) / len(treatment_values) - sum(control_values) / len(control_values))
    return null or [0.0], "exact_permutation_null"


def _two_sided_permutation_p_value(null_ates: list[float], *, observed: float) -> float:
    extreme = sum(1 for value in null_ates if abs(value) >= abs(observed))
    return extreme / len(null_ates)
