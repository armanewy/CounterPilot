from __future__ import annotations

from typing import Any

from behavior_lab.core import stable_hash, utc_now
from behavior_lab.money.contracts import Action, FinancialDecisionContract
from behavior_lab.money.ledger import MoneyLedgerEntry


def offerlab_shadow_contract(*, pilot_id: str, decision_deadline: str) -> FinancialDecisionContract:
    actions = [
        Action(action_id="abstain", action_type="no_action", parameters={"reason": "insufficient_or_shadow_only"}),
        Action(action_id="decline", action_type="seller_offer_response", parameters={"response": "decline"}),
        Action(action_id="accept", action_type="seller_offer_response", parameters={"response": "accept"}),
        Action(
            action_id="counter",
            action_type="seller_offer_response",
            parameters={"response": "counter", "bounded_values_only": True},
        ),
    ]
    return FinancialDecisionContract(
        contract_id=f"offerlab_shadow_{stable_hash(pilot_id)[:12]}",
        domain="seller",
        target={
            "type": "seller_offer_shadow_policy",
            "pilot_id_hash": stable_hash(pilot_id),
            "primary_metric": "mature_contribution_margin",
        },
        decision_horizon="seller_pilot_shadow_window",
        decision_deadline=decision_deadline,
        available_actions=actions,
        no_action_id="abstain",
        payoff_specification={
            "gross_value": "seller mature contribution margin",
            "net_value": "gross minus actual fees, shipping, refunds, and cost basis",
        },
        cost_policy={
            "material_cost_fields": ["fees", "shipping", "cost_basis", "refunds"],
            "unknown_material_cost": "ineligible",
            "imputation_allowed": False,
        },
        risk_policy={"paper_only": True, "causal_lift_claim_allowed": False},
        liquidity_policy={"not_applicable": "seller-owned historical offer decisions"},
        resolution_source={"type": "seller_supplied_pilot_ledger", "requires_external_ledger": True},
        data_cutoff_policy={"source": "latest imported seller pilot ledger version", "as_of_required": True},
        prospective_requirement={"required_before_real_action": True, "mode": "shadow_only"},
        notification_threshold={"notifications_allowed": False},
        paper_only=True,
        contract_version="offerlab_shadow_contract.v1",
    )


def offerlab_shadow_entry(
    *,
    audit: dict[str, Any],
    shadow_report: dict[str, Any],
    contract: FinancialDecisionContract,
    decision_timestamp: str | None = None,
    data_cutoff: str | None = None,
) -> MoneyLedgerEntry:
    """Represent a seller-pilot shadow decision without mutating seller state."""

    timestamp = decision_timestamp or shadow_report.get("generated_at") or utc_now()
    cutoff = data_cutoff or shadow_report.get("generated_at") or timestamp
    readiness = audit.get("readiness_gate", {})
    gaps = audit.get("data_quality_gaps", {})
    missing_costs = bool(
        gaps.get("missing_cost_basis_listing_ids")
        or gaps.get("orders_missing_actual_fees")
        or gaps.get("orders_missing_shipping_costs")
    )
    selected = "abstain"
    reasons = list(shadow_report.get("shadow_recommendations", {}).get("reasons", []))
    if shadow_report.get("shadow_recommendations", {}).get("action") == "prepare_shadow_policy":
        reasons.append("shadow policy preparation represented as no-action until a later approval wave")
    material_costs_known = not missing_costs
    ineligibility_reasons = [] if material_costs_known else ["unknown_material_seller_costs"]
    mature_margin = audit.get("mature_contribution_margin", {})
    artifact_hashes = {
        "latest_import_hash": str(audit.get("latest_import_hash")),
        "shadow_report_hash": str(shadow_report.get("report_hash")),
    }
    return MoneyLedgerEntry(
        decision_id=f"offerlab_shadow_{stable_hash({'pilot': audit.get('pilot_id'), 'import': audit.get('latest_import_hash')})[:16]}",
        contract_hash=contract.contract_hash(),
        decision_timestamp=timestamp,
        data_cutoff=cutoff,
        target=contract.target,
        action_alternatives=[action.action_id for action in contract.available_actions],
        selected_action=selected,
        no_action_alternative=contract.no_action_id,
        capital_required=0.0,
        maximum_possible_loss=0.0,
        expected_gross_value=0.0,
        uncertainty_adjustment=0.0,
        fees=0.0 if material_costs_known else None,
        slippage=0.0,
        shipping=0.0 if material_costs_known else None,
        taxes_or_tax_assumption_reference="not_applicable_seller_shadow",
        holding_costs=0.0,
        return_refund_allowance=0.0 if material_costs_known else None,
        research_api_cost=0.0,
        conservative_expected_net_value=0.0 if material_costs_known else None,
        decision_deadline=contract.decision_deadline,
        feature_program_hash=stable_hash({"adapter": "offerlab_shadow_entry.v1"}),
        evidence_state="paper_decision",
        designation="paper",
        mechanically_defined_no_action_outcome={
            "action": "abstain",
            "seller_mutation": False,
            "realized_commercial_evidence": False,
        },
        economic_event_key=f"offerlab_shadow:{stable_hash({'pilot': audit.get('pilot_id'), 'import': audit.get('latest_import_hash')})[:16]}",
        provenance={
            "source_id": "offerlab_seller_pilot",
            "strategy_id": "seller_shadow_abstain_until_validated",
            "evidence_class": "prospective_shadow_evidence",
            "read_only": True,
            "executes_seller_actions": False,
            "causal_lift_claimed": False,
            "historical_recommended_action": shadow_report.get("shadow_recommendations", {}).get("action"),
            "abstention_reasons": reasons,
            "mature_contribution_margin": mature_margin,
            "cancellation_return_effects": audit.get("cancellation_return_effects", {}),
            "readiness_gate": readiness,
            "data_quality_gaps": shadow_report.get("profit_and_loss_reconstruction", {}).get("data_quality_gaps", {}),
        },
        artifact_hashes=artifact_hashes,
        assumption_versions={
            "offerlab_adapter": "v1",
            "money_ledger_contract": "v1",
        },
        material_costs_known=material_costs_known,
        ineligibility_reasons=ineligibility_reasons,
    )
