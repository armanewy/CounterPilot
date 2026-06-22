from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable


MONEY_QUANT = Decimal("0.01")


class UnknownMaterialCostError(ValueError):
    pass


@dataclass(frozen=True)
class AccountingResult:
    eligible: bool
    gross_value: float
    total_costs: float | None
    net_value: float | None
    conservative_expected_net_value: float | None
    missing_material_costs: list[str]
    cost_breakdown: dict[str, float | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "gross_value": self.gross_value,
            "total_costs": self.total_costs,
            "net_value": self.net_value,
            "conservative_expected_net_value": self.conservative_expected_net_value,
            "missing_material_costs": list(self.missing_material_costs),
            "cost_breakdown": dict(self.cost_breakdown),
        }


def compute_decision_accounting(
    *,
    gross_value: float,
    uncertainty_adjustment: float = 0.0,
    material_cost_fields: Iterable[str] = (),
    strict: bool = False,
    **costs: float | None,
) -> AccountingResult:
    """Compute deterministic gross/net values without imputing unknown costs.

    Any field named in `material_cost_fields` must be present and non-None. A
    missing material cost makes the decision ineligible; with `strict=True`,
    that same condition raises `UnknownMaterialCostError`.
    """

    missing = [field for field in material_cost_fields if costs.get(field) is None]
    if missing:
        if strict:
            raise UnknownMaterialCostError(f"unknown material costs: {', '.join(sorted(missing))}")
        return AccountingResult(
            eligible=False,
            gross_value=_money(gross_value),
            total_costs=None,
            net_value=None,
            conservative_expected_net_value=None,
            missing_material_costs=sorted(missing),
            cost_breakdown={field: _maybe_money(value) for field, value in sorted(costs.items())},
        )
    normalized = {field: _money(value or 0.0) for field, value in sorted(costs.items())}
    total_costs = _money(sum(Decimal(str(value)) for value in normalized.values()))
    net = _money(Decimal(str(gross_value)) - Decimal(str(total_costs)))
    conservative = _money(Decimal(str(net)) - Decimal(str(uncertainty_adjustment)))
    return AccountingResult(
        eligible=True,
        gross_value=_money(gross_value),
        total_costs=total_costs,
        net_value=net,
        conservative_expected_net_value=conservative,
        missing_material_costs=[],
        cost_breakdown=normalized,
    )


def maximum_drawdown(values: Iterable[float]) -> dict[str, Any]:
    curve = [float(value) for value in values]
    if not curve:
        return {"maximum_drawdown": 0.0, "peak_index": None, "trough_index": None}
    peak = curve[0]
    peak_index = 0
    best_drawdown = 0.0
    best_peak_index = 0
    best_trough_index = 0
    for index, value in enumerate(curve):
        if value > peak:
            peak = value
            peak_index = index
        drawdown = peak - value
        if drawdown > best_drawdown:
            best_drawdown = drawdown
            best_peak_index = peak_index
            best_trough_index = index
    return {
        "maximum_drawdown": _money(best_drawdown),
        "peak_index": best_peak_index,
        "trough_index": best_trough_index,
    }


def summarize_money_entries(entries: Iterable[Any]) -> dict[str, Any]:
    materialized = [entry.to_dict() if hasattr(entry, "to_dict") else dict(entry) for entry in entries]
    designations = {entry.get("designation") for entry in materialized if entry.get("designation")}
    if len(designations) > 1:
        raise ValueError("paper and real outcomes cannot be summarized together")
    unique_decisions = {entry["decision_id"]: entry for entry in materialized}
    action_counts: dict[str, int] = {}
    no_action_count = 0
    by_contract: dict[str, float] = {}
    by_strategy: dict[str, float] = {}
    by_source: dict[str, float] = {}
    ordered_values = []
    capital_at_risk = 0.0
    maximum_possible_loss = 0.0
    max_single_decision_loss = 0.0
    for entry in sorted(unique_decisions.values(), key=lambda item: (item.get("decision_timestamp"), item["decision_id"])):
        action = str(entry.get("selected_action"))
        action_counts[action] = action_counts.get(action, 0) + 1
        if action == entry.get("no_action_alternative"):
            no_action_count += 1
        value = entry.get("realized_net_value")
        if value is None:
            value = entry.get("conservative_expected_net_value")
        value = float(value or 0.0)
        ordered_values.append(value)
        contract = str(entry.get("contract_hash"))
        by_contract[contract] = _money(by_contract.get(contract, 0.0) + value)
        provenance = entry.get("provenance") or {}
        strategy = str(provenance.get("strategy_id", "unknown"))
        source = str(provenance.get("source_id", "unknown"))
        by_strategy[strategy] = _money(by_strategy.get(strategy, 0.0) + value)
        by_source[source] = _money(by_source.get(source, 0.0) + value)
        capital_at_risk = _money(capital_at_risk + float(entry.get("capital_required") or 0.0))
        loss = float(entry.get("maximum_possible_loss") or 0.0)
        maximum_possible_loss = _money(maximum_possible_loss + loss)
        max_single_decision_loss = _money(max(max_single_decision_loss, loss))
    return {
        "opportunity_count": len(unique_decisions),
        "action_frequency": action_counts,
        "no_action_frequency": no_action_count,
        "capital_at_risk": capital_at_risk,
        "maximum_possible_loss": maximum_possible_loss,
        "max_single_decision_loss": max_single_decision_loss,
        "value_by_contract": by_contract,
        "value_by_strategy": by_strategy,
        "value_by_source": by_source,
        "maximum_drawdown": maximum_drawdown(_cumulative(ordered_values)),
        "designation": next(iter(designations)) if designations else None,
    }


def _cumulative(values: list[float]) -> list[float]:
    total = Decimal("0")
    output = []
    for value in values:
        total += Decimal(str(value))
        output.append(_money(total))
    return output


def _maybe_money(value: float | None) -> float | None:
    if value is None:
        return None
    return _money(value)


def _money(value: float | int | Decimal) -> float:
    return float(Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP))
