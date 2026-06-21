from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from behavior_lab.data_sources.registry import default_registry


@dataclass(frozen=True)
class UpliftReport:
    source_id: str
    production_export_allowed: bool
    treatment_rate: float
    control_outcome_rate: float
    treatment_outcome_rate: float
    average_treatment_effect: float
    negative_control_ate: float

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
    # Deterministic negative control: swap every other treatment label to test that the report does not invent large uplift.
    negative_rows = [dict(row, treatment=(index % 2)) for index, row in enumerate(rows)]
    negative_t = [float(row[outcome_key]) for row in negative_rows if int(row["treatment"]) == 1]
    negative_c = [float(row[outcome_key]) for row in negative_rows if int(row["treatment"]) == 0]
    negative_ate = (sum(negative_t) / len(negative_t)) - (sum(negative_c) / len(negative_c))
    permission = default_registry().check("criteo_uplift", "production_export")
    return UpliftReport(
        source_id="criteo_uplift",
        production_export_allowed=permission.allowed,
        treatment_rate=float(validation["treatment_rate"]),
        control_outcome_rate=control_rate,
        treatment_outcome_rate=treatment_rate,
        average_treatment_effect=treatment_rate - control_rate,
        negative_control_ate=negative_ate,
    ).to_dict()
