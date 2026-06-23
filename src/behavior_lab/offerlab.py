from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Protocol

from behavior_lab.core import parse_time, stable_hash
from behavior_lab.ledger import ImmutableLedger
from behavior_lab.temporal import assert_feature_map_is_pre_decision


OFFERLAB_CAMPAIGN_ID = "campaign_002_ebay_seller_offers"
OFFERLAB_SCHEMA_VERSION = "offerlab_decision_snapshot.v2"
OFFERLAB_CAMPAIGN_SCHEMA_VERSION = "0.2"
OFFERLAB_LEDGER_RECORD_TYPE = "campaign_002_offer_decision"
OFFERLAB_CAMPAIGN_RECORD_ID = f"campaign_definition_{OFFERLAB_CAMPAIGN_ID}"

OFFERLAB_ACTIONS = {
    "accept",
    "decline",
    "counter_at_amount",
    "send_seller_offer_at_amount",
    "wait",
    "reduce_listing_price",
}

DECISION_CHANNELS = {
    "buyer_initiated_best_offer",
    "seller_initiated_offer",
    "seller_counteroffer",
    "listing_price_reduction",
    "passive_wait",
}

TRAFFIC_TRENDS = {"declining", "flat", "increasing", "unknown"}
MAX_FRESH_TRAFFIC_AGE_HOURS = 72.0
MIN_COMPARABLE_MATURE_CASES = 10

POST_DECISION_OUTCOME_KEYS = {
    "offer_received",
    "seller_accepted",
    "buyer_paid",
    "countered",
    "ignored",
    "sold_within_48_hours",
    "sold_within_7_days",
    "final_sale_price",
    "days_to_sale",
    "unpaid_order",
    "order_cancelled",
    "returned",
    "return_window_matured",
    "actual_ebay_fees",
    "provisional_margin",
    "mature_margin",
    "margin_maturity_date",
}


class OfferLabError(ValueError):
    pass


class EbayReadOnlyAdapter(Protocol):
    """Boundary for a future official eBay API adapter.

    Implementations may read active listings, offers, traffic, completed orders,
    and seller finance data. They must not accept, decline, counter, discount,
    or otherwise mutate marketplace state.
    """

    name: str

    def decision_snapshots(self) -> list[dict[str, Any]]:
        ...


@dataclass(frozen=True)
class OfferLabIngestResult:
    campaign_id: str
    imported: int
    skipped_existing: int
    ledger: str
    decision_hashes: list[str]


@dataclass(frozen=True)
class EconomicConfig:
    marketplace_fee_rate: float = 0.1325
    holding_cost_per_day: float = 0.0
    return_risk_cost_rate: float = 0.0
    horizon_days: int = 7

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "EconomicConfig":
        if value is None:
            return cls()
        config = cls(
            marketplace_fee_rate=float(value.get("marketplace_fee_rate", cls.marketplace_fee_rate)),
            holding_cost_per_day=float(value.get("holding_cost_per_day", cls.holding_cost_per_day)),
            return_risk_cost_rate=float(value.get("return_risk_cost_rate", cls.return_risk_cost_rate)),
            horizon_days=int(value.get("horizon_days", cls.horizon_days)),
        )
        if not 0 <= config.marketplace_fee_rate < 1:
            raise OfferLabError("marketplace_fee_rate must be in [0, 1)")
        if config.holding_cost_per_day < 0:
            raise OfferLabError("holding_cost_per_day may not be negative")
        if not 0 <= config.return_risk_cost_rate < 1:
            raise OfferLabError("return_risk_cost_rate must be in [0, 1)")
        if config.horizon_days <= 0:
            raise OfferLabError("horizon_days must be positive")
        return config


def campaign_002_definition() -> dict[str, Any]:
    return {
        "campaign_id": OFFERLAB_CAMPAIGN_ID,
        "campaign_schema_version": OFFERLAB_CAMPAIGN_SCHEMA_VERSION,
        "title": "Campaign 002 - eBay seller offers",
        "working_names": ["OfferLab", "Counterpilot"],
        "commercial_goal": "Optimize seller-side offer and pricing decisions for realized contribution margin.",
        "stage": "read_only_profit_audit",
        "primary_metric": "mature_contribution_margin_per_listing_day",
        "pre_decision_context": {
            "listing_id": "string",
            "decision_channel": "buyer_initiated_best_offer | seller_initiated_offer | seller_counteroffer | listing_price_reduction | passive_wait",
            "category": "string",
            "condition": "string",
            "asking_price": "positive number",
            "seller_cost_basis": "non-negative number or null",
            "minimum_net_proceeds": "non-negative number",
            "days_active": "non-negative integer",
            "impressions_7d": "non-negative integer",
            "views_7d": "non-negative integer",
            "traffic_trend": "declining | flat | increasing | unknown",
            "traffic_data_age_hours": "non-negative number",
            "prior_offer_count": "non-negative integer",
            "buyer_offer_amount": "positive number or null",
            "offer_to_asking_ratio": "number in [0, 1] or null",
            "eligible_interested_buyers": "non-negative integer",
            "shipping_cost": "non-negative number",
            "promotion_cost": "non-negative number",
            "comparable_price_band": "object with optional low, median, high non-negative numbers",
            "inventory_quantity": "non-negative integer",
            "seller_urgency": "integer 0..3",
        },
        "actions": sorted(OFFERLAB_ACTIONS),
        "protected_outcomes": sorted(POST_DECISION_OUTCOME_KEYS),
        "guardrails": [
            "read-only until prospective evidence exists",
            "never optimize acceptance rate without margin",
            "never automate below seller floor",
            "do not use post-decision outcomes in recommendations",
            "separate seller_accepted from buyer_paid",
            "score profit on mature return-window outcomes, not provisional sales",
            "do not pool buyer-originated Best Offers with other decision channels",
            "abstain when cost basis, traffic freshness, or comparable evidence is missing",
            "treat retrospective policy differences as hypotheses, not causal proof",
        ],
    }


def write_campaign_002_template(path: str | Path) -> dict[str, Any]:
    template = sample_offer_decision_snapshot()
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(template, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return template


def sample_offer_decision_snapshot() -> dict[str, Any]:
    return {
        "schema_version": OFFERLAB_SCHEMA_VERSION,
        "campaign_id": OFFERLAB_CAMPAIGN_ID,
        "decision_id": "offerlab_sample_001",
        "seller_id": "example_seller",
        "decision_time": "2026-06-21T10:00:00-04:00",
        "observation_cutoff": "2026-06-21T10:00:00-04:00",
        "listing_id": "1234567890",
        "pre_decision_context": {
            "listing_id": "1234567890",
            "decision_channel": "buyer_initiated_best_offer",
            "category": "refurbished electronics",
            "condition": "used",
            "asking_price": 900.0,
            "seller_cost_basis": 520.0,
            "minimum_net_proceeds": 580.0,
            "days_active": 26,
            "impressions_7d": 910,
            "views_7d": 81,
            "traffic_trend": "declining",
            "traffic_data_age_hours": 6.0,
            "prior_offer_count": 2,
            "buyer_offer_amount": 720.0,
            "offer_to_asking_ratio": 0.8,
            "eligible_interested_buyers": 3,
            "shipping_cost": 34.0,
            "promotion_cost": 9.0,
            "comparable_price_band": {"low": 735.0, "median": 795.0, "high": 860.0},
            "inventory_quantity": 1,
            "seller_urgency": 1,
        },
        "available_actions": [
            {"action": "accept", "amount": 720.0},
            {"action": "counter_at_amount", "amount": 760.0},
            {"action": "counter_at_amount", "amount": 790.0},
            {"action": "wait"},
        ],
        "action_taken": None,
        "protected_outcome": None,
        "provenance": {
            "source": "manual_example",
            "api_boundaries": [
                "Trading API Best Offer read/respond later",
                "Sell Negotiation seller-initiated offers later",
                "Sell Analytics traffic reports later",
                "Sell Finances transactions later",
            ],
        },
    }


def load_offerlab_snapshots(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if not text.strip():
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if payload is None:
        snapshots: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise OfferLabError(f"Invalid JSONL at line {line_number}") from exc
            if not isinstance(item, dict):
                raise OfferLabError(f"Snapshot at line {line_number} must be a JSON object")
            snapshots.append(item)
        return snapshots
    if isinstance(payload, dict) and isinstance(payload.get("snapshots"), list):
        snapshots = payload["snapshots"]
        if not all(isinstance(item, dict) for item in snapshots):
            raise OfferLabError("snapshots entries must be JSON objects")
        return list(snapshots)
    if isinstance(payload, list):
        if not all(isinstance(item, dict) for item in payload):
            raise OfferLabError("Snapshot array entries must be JSON objects")
        return list(payload)
    if isinstance(payload, dict):
        return [payload]
    raise OfferLabError("Expected a JSON object, JSON array, or JSONL file")


def validate_offer_decision_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot.get("schema_version") != OFFERLAB_SCHEMA_VERSION:
        raise OfferLabError(f"schema_version must be {OFFERLAB_SCHEMA_VERSION!r}")
    if snapshot.get("campaign_id") != OFFERLAB_CAMPAIGN_ID:
        raise OfferLabError(f"campaign_id must be {OFFERLAB_CAMPAIGN_ID!r}")
    for key in ["decision_id", "seller_id", "listing_id"]:
        if not isinstance(snapshot.get(key), str) or not str(snapshot[key]).strip():
            raise OfferLabError(f"{key} must be a non-empty string")
    decision_time = str(snapshot.get("decision_time", ""))
    observation_cutoff = str(snapshot.get("observation_cutoff", ""))
    decision_dt = parse_time(decision_time)
    cutoff_dt = parse_time(observation_cutoff)
    if cutoff_dt > decision_dt:
        raise OfferLabError("observation_cutoff may not occur after decision_time")

    context = snapshot.get("pre_decision_context")
    if not isinstance(context, dict):
        raise OfferLabError("pre_decision_context must be an object")
    _validate_context(context)
    assert_feature_map_is_pre_decision(context, target_name="mature_margin")

    actions = snapshot.get("available_actions")
    if not isinstance(actions, list) or not actions:
        raise OfferLabError("available_actions must be a non-empty list")
    for action in actions:
        _normalize_action(action)

    action_taken = snapshot.get("action_taken")
    if action_taken is not None:
        normalized = _normalize_action(action_taken)
        normalized_actions = [_normalize_action(action) for action in actions]
        if normalized not in normalized_actions:
            raise OfferLabError("action_taken must appear in available_actions")

    outcome = snapshot.get("protected_outcome")
    if outcome is not None:
        if action_taken is None:
            raise OfferLabError("action_taken is required when protected_outcome is present")
        if not isinstance(outcome, dict):
            raise OfferLabError("protected_outcome must be null or an object")
        _validate_outcome(outcome)
    provenance = snapshot.get("provenance")
    if not isinstance(provenance, dict):
        raise OfferLabError("provenance must be an object")
    return snapshot


def decision_hash(snapshot: dict[str, Any]) -> str:
    body = dict(snapshot)
    body.pop("decision_hash", None)
    return stable_hash(body)


def with_decision_hash(snapshot: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(snapshot)
    prepared["decision_hash"] = decision_hash(prepared)
    return prepared


def ingest_offerlab_snapshots(path: str | Path, *, data_dir: str | Path) -> OfferLabIngestResult:
    ledger = ImmutableLedger(Path(data_dir) / "ledger.jsonl")
    _ensure_offerlab_definition(ledger)
    imported = 0
    skipped = 0
    hashes: list[str] = []
    for raw_snapshot in load_offerlab_snapshots(path):
        snapshot = with_decision_hash(validate_offer_decision_snapshot(dict(raw_snapshot)))
        record_id = f"offerlab_decision_{snapshot['decision_id']}"
        existing = ledger.find_record(record_id, OFFERLAB_LEDGER_RECORD_TYPE)
        if existing is not None:
            if existing.get("payload") != snapshot:
                raise OfferLabError(f"Existing decision {record_id!r} differs from imported snapshot")
            skipped += 1
            hashes.append(str(snapshot["decision_hash"]))
            continue
        ledger.append(OFFERLAB_LEDGER_RECORD_TYPE, snapshot, record_id=record_id, unique_record_id=True)
        imported += 1
        hashes.append(str(snapshot["decision_hash"]))
    ledger.verify_hash_chain()
    return OfferLabIngestResult(
        campaign_id=OFFERLAB_CAMPAIGN_ID,
        imported=imported,
        skipped_existing=skipped,
        ledger=str(ledger.path),
        decision_hashes=hashes,
    )


def profit_audit(data_dir: str | Path) -> dict[str, Any]:
    ledger = ImmutableLedger(Path(data_dir) / "ledger.jsonl")
    records = ledger.payloads(OFFERLAB_LEDGER_RECORD_TYPE)
    with_outcomes = [record for record in records if isinstance(record.get("protected_outcome"), dict)]
    by_action: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in with_outcomes:
        action = _normalize_action(record.get("action_taken"))
        grouped[action["action"]].append(record)
    for action_name, items in sorted(grouped.items()):
        by_action[action_name] = _summarize_action_records(items)
    ledger.verify_hash_chain()
    mature_margins = [_mature_margin(record["protected_outcome"]) for record in with_outcomes]
    mature_margins = [value for value in mature_margins if value is not None]
    provisional_margins = [_provisional_margin(record["protected_outcome"]) for record in with_outcomes]
    provisional_margins = [value for value in provisional_margins if value is not None]
    return {
        "campaign_id": OFFERLAB_CAMPAIGN_ID,
        "ledger": str(ledger.path.resolve()),
        "ledger_valid": True,
        "decisions": len(records),
        "decisions_with_outcomes": len(with_outcomes),
        "paid_outcomes": sum(1 for record in with_outcomes if _buyer_paid(record["protected_outcome"])),
        "mature_paid_outcomes": sum(1 for record in with_outcomes if _mature_paid_outcome(record["protected_outcome"])),
        "seller_accepted_unpaid_outcomes": sum(
            1 for record in with_outcomes if record["protected_outcome"]["seller_accepted"] and not record["protected_outcome"]["buyer_paid"]
        ),
        "total_mature_contribution_margin": round(sum(mature_margins), 2),
        "total_provisional_contribution_margin": round(sum(provisional_margins), 2),
        "by_action": by_action,
        "by_decision_channel": _group_summary(with_outcomes, lambda item: str(item["pre_decision_context"]["decision_channel"])),
        "data_quality": _data_quality(records),
        "primary_metric": "mature_contribution_margin_per_listing_day",
    }


def profit_audit_report(data_dir: str | Path) -> dict[str, Any]:
    ledger = ImmutableLedger(Path(data_dir) / "ledger.jsonl")
    records = ledger.payloads(OFFERLAB_LEDGER_RECORD_TYPE)
    ledger.verify_hash_chain()
    with_outcomes = [record for record in records if isinstance(record.get("protected_outcome"), dict)]
    audit = profit_audit(data_dir)
    return {
        "campaign_id": OFFERLAB_CAMPAIGN_ID,
        "ledger": audit["ledger"],
        "ledger_valid": True,
        "retrospective_only": True,
        "data_quality": audit["data_quality"],
        "historical_policy_audit": _historical_policy_audit(with_outcomes),
        "profit_frontier": _profit_frontier(audit),
        "missed_opportunities": _missed_opportunities(with_outcomes),
        "proposed_policy": _proposed_policy(audit, with_outcomes),
        "prospective_test": _prospective_test_plan(records),
        "limitations": [
            "Retrospective differences are not causal evidence.",
            "Profit should be scored only after buyer payment, fees, and return-window maturity are known.",
            "Buyer-originated Best Offers, seller-initiated offers, price reductions, and wait policies must stay separated.",
            "Recommendations must abstain when cost basis, fee, traffic, or comparable mature outcome evidence is incomplete.",
        ],
    }


def write_profit_audit_report(data_dir: str | Path, output_path: str | Path) -> dict[str, Any]:
    report = profit_audit_report(data_dir)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.suffix.lower() == ".md":
        destination.write_text(render_profit_audit_markdown(report), encoding="utf-8")
    else:
        destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output": str(destination.resolve()), "campaign_id": OFFERLAB_CAMPAIGN_ID, "data_quality_score": report["data_quality"]["score"]}


def render_profit_audit_markdown(report: dict[str, Any]) -> str:
    quality = report["data_quality"]
    lines = [
        "# OfferLab Profit Audit",
        "",
        f"Campaign: `{report['campaign_id']}`",
        f"Ledger valid: `{report['ledger_valid']}`",
        f"Retrospective only: `{report['retrospective_only']}`",
        "",
        "## Data Quality",
        "",
        f"- Score: {quality['score']}",
        f"- Cost-basis coverage: {quality['cost_basis_coverage']}",
        f"- Actual eBay fee coverage: {quality['actual_ebay_fee_coverage']}",
        f"- Mature return-window coverage: {quality['mature_return_window_coverage']}",
        f"- Fresh traffic coverage: {quality['fresh_traffic_coverage']}",
        f"- Mature paid outcomes: {quality['mature_paid_outcomes']}",
    ]
    for warning in quality["warnings"]:
        lines.append(f"- Warning: {warning}")

    lines.extend(["", "## Historical Policy Audit", ""])
    lines.extend(_markdown_table(report["historical_policy_audit"]["offer_ratio_buckets"]))
    lines.extend(["", "## Profit Frontier", ""])
    lines.extend(_markdown_table(report["profit_frontier"]))
    lines.extend(["", "## Missed Opportunities", ""])
    if report["missed_opportunities"]:
        lines.extend(_markdown_table(report["missed_opportunities"]))
    else:
        lines.append("No retrospective missed-opportunity candidates met the conservative filter.")
    lines.extend(["", "## Proposed Policy", ""])
    policy = report["proposed_policy"]
    lines.append(f"Status: `{policy['status']}`")
    for item in policy.get("rules", []):
        lines.append(f"- {item}")
    for item in policy.get("guardrails", []):
        lines.append(f"- Guardrail: {item}")
    lines.extend(["", "## Prospective Test", ""])
    test_plan = report["prospective_test"]
    lines.append(f"Status: `{test_plan['status']}`")
    for item in test_plan.get("design", []):
        lines.append(f"- {item}")
    for item in test_plan.get("guardrails", []):
        lines.append(f"- Guardrail: {item}")
    lines.extend(["", "## Limitations", ""])
    for item in report["limitations"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def recommend_offer_action(
    snapshot: dict[str, Any],
    *,
    data_dir: str | Path | None = None,
    config: dict[str, Any] | None = None,
    min_comparable_mature_cases: int = MIN_COMPARABLE_MATURE_CASES,
) -> dict[str, Any]:
    snapshot = validate_offer_decision_snapshot(dict(snapshot))
    economic = EconomicConfig.from_mapping(config)
    records = _historical_records(data_dir)
    comparable_mature = _comparable_mature_records(records, snapshot)
    evaluated = [
        _evaluate_action(_normalize_action(action), snapshot["pre_decision_context"], economic)
        for action in snapshot["available_actions"]
    ]
    reasons = _abstention_reasons(
        snapshot,
        data_dir=data_dir,
        historical_records=records,
        comparable_mature_records=comparable_mature,
        evaluated=evaluated,
        min_comparable_mature_cases=min_comparable_mature_cases,
    )
    if reasons:
        recommendation = {
            "status": "abstain",
            "reasons": reasons,
            "confidence": "none",
            "source": "read_only_evidence_gate_v2",
        }
    else:
        viable = [
            item
            for item in evaluated
            if not item["violates_floor"] and item["expected_contribution_margin"] is not None
        ]
        best = max(viable, key=lambda item: float(item["expected_contribution_margin"]))
        accept = next((item for item in evaluated if item["action"] == "accept"), None)
        advantage = None
        if accept is not None and accept["expected_contribution_margin"] is not None:
            advantage = round(float(best["expected_contribution_margin"]) - float(accept["expected_contribution_margin"]), 2)
        recommendation = {
            "status": "recommend",
            "action": best["action"],
            "amount": best.get("amount"),
            "expected_contribution_margin": best["expected_contribution_margin"],
            "expected_advantage_over_accept_now": advantage,
            "confidence": _confidence_label(len(comparable_mature), evaluated),
            "source": "deterministic_read_only_arithmetic_v2",
        }
    return {
        "campaign_id": OFFERLAB_CAMPAIGN_ID,
        "decision_id": snapshot["decision_id"],
        "recommendation": recommendation,
        "evaluated_actions": evaluated,
        "historical_cases_considered": len(records),
        "comparable_mature_cases_considered": len(comparable_mature),
        "evidence_scope": {
            "decision_channel": snapshot["pre_decision_context"]["decision_channel"],
            "category": snapshot["pre_decision_context"]["category"],
            "minimum_comparable_mature_cases": min_comparable_mature_cases,
        },
        "execute_action": False,
        "guardrail": "read-only output; seller must choose the action",
    }


def write_recommendation(snapshot_path: str | Path, output_path: str | Path, *, data_dir: str | Path | None = None) -> dict[str, Any]:
    snapshots = load_offerlab_snapshots(snapshot_path)
    if len(snapshots) != 1:
        raise OfferLabError("Recommendation requires exactly one snapshot")
    result = recommend_offer_action(snapshots[0], data_dir=data_dir)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _validate_context(context: dict[str, Any]) -> None:
    required = {
        "listing_id",
        "decision_channel",
        "category",
        "condition",
        "asking_price",
        "seller_cost_basis",
        "minimum_net_proceeds",
        "days_active",
        "impressions_7d",
        "views_7d",
        "traffic_trend",
        "traffic_data_age_hours",
        "prior_offer_count",
        "buyer_offer_amount",
        "offer_to_asking_ratio",
        "eligible_interested_buyers",
        "shipping_cost",
        "promotion_cost",
        "comparable_price_band",
        "inventory_quantity",
        "seller_urgency",
    }
    missing = sorted(required - set(context))
    extra = sorted(set(context) - required)
    if missing:
        raise OfferLabError(f"Missing pre-decision context fields: {missing}")
    if extra:
        raise OfferLabError(f"Unexpected pre-decision context fields: {extra}")
    for key in ["listing_id", "category", "condition"]:
        if not isinstance(context[key], str) or not context[key].strip():
            raise OfferLabError(f"{key} must be a non-empty string")
    if context["decision_channel"] not in DECISION_CHANNELS:
        raise OfferLabError(f"decision_channel must be one of {sorted(DECISION_CHANNELS)}")
    if _number(context["asking_price"]) <= 0:
        raise OfferLabError("asking_price must be positive")
    if context["seller_cost_basis"] is not None and _number(context["seller_cost_basis"]) < 0:
        raise OfferLabError("seller_cost_basis may not be negative")
    for key in ["minimum_net_proceeds", "shipping_cost", "promotion_cost", "traffic_data_age_hours"]:
        if _number(context[key]) < 0:
            raise OfferLabError(f"{key} may not be negative")
    for key in ["days_active", "impressions_7d", "views_7d", "prior_offer_count", "eligible_interested_buyers", "inventory_quantity"]:
        _nonnegative_int(context[key], key)
    if int(context["seller_urgency"]) not in {0, 1, 2, 3}:
        raise OfferLabError("seller_urgency must be in 0..3")
    if context["traffic_trend"] not in TRAFFIC_TRENDS:
        raise OfferLabError(f"traffic_trend must be one of {sorted(TRAFFIC_TRENDS)}")
    buyer_offer = context["buyer_offer_amount"]
    ratio = context["offer_to_asking_ratio"]
    if buyer_offer is None:
        if ratio is not None:
            raise OfferLabError("offer_to_asking_ratio must be null when buyer_offer_amount is null")
        if context["decision_channel"] == "buyer_initiated_best_offer":
            raise OfferLabError("buyer_initiated_best_offer requires buyer_offer_amount")
    else:
        if _number(buyer_offer) <= 0:
            raise OfferLabError("buyer_offer_amount must be positive when present")
        observed_ratio = _number(buyer_offer) / _number(context["asking_price"])
        if ratio is None or not math.isclose(_number(ratio), observed_ratio, rel_tol=1e-3, abs_tol=1e-3):
            raise OfferLabError("offer_to_asking_ratio must match buyer_offer_amount / asking_price")
    band = context["comparable_price_band"]
    if not isinstance(band, dict):
        raise OfferLabError("comparable_price_band must be an object")
    for key, value in band.items():
        if key not in {"low", "median", "high"}:
            raise OfferLabError("comparable_price_band may only contain low, median, and high")
        if value is not None and _number(value) < 0:
            raise OfferLabError(f"comparable_price_band.{key} may not be negative")


def _validate_outcome(outcome: dict[str, Any]) -> None:
    missing = sorted(POST_DECISION_OUTCOME_KEYS - set(outcome))
    extra = sorted(set(outcome) - POST_DECISION_OUTCOME_KEYS)
    if missing:
        raise OfferLabError(f"Missing protected outcome fields: {missing}")
    if extra:
        raise OfferLabError(f"Unexpected protected outcome fields: {extra}")
    for key in [
        "offer_received",
        "seller_accepted",
        "buyer_paid",
        "countered",
        "ignored",
        "sold_within_48_hours",
        "sold_within_7_days",
        "unpaid_order",
        "order_cancelled",
        "returned",
        "return_window_matured",
    ]:
        if not isinstance(outcome[key], bool):
            raise OfferLabError(f"{key} must be boolean")
    for key in ["final_sale_price", "actual_ebay_fees", "provisional_margin", "mature_margin", "days_to_sale"]:
        if outcome[key] is not None:
            _number(outcome[key])
    if outcome["days_to_sale"] is not None and float(outcome["days_to_sale"]) < 0:
        raise OfferLabError("days_to_sale may not be negative")
    if outcome["actual_ebay_fees"] is not None and float(outcome["actual_ebay_fees"]) < 0:
        raise OfferLabError("actual_ebay_fees may not be negative")
    if outcome["buyer_paid"] and outcome["final_sale_price"] is None:
        raise OfferLabError("buyer_paid outcomes require final_sale_price")
    if outcome["buyer_paid"] and outcome["unpaid_order"]:
        raise OfferLabError("buyer_paid may not be true when unpaid_order is true")
    if outcome["return_window_matured"]:
        if outcome["buyer_paid"] and outcome["mature_margin"] is None:
            raise OfferLabError("mature paid outcomes require mature_margin")
    elif outcome["mature_margin"] is not None:
        raise OfferLabError("mature_margin must be null until return_window_matured is true")
    maturity_date = outcome["margin_maturity_date"]
    if maturity_date is not None and (not isinstance(maturity_date, str) or not maturity_date.strip()):
        raise OfferLabError("margin_maturity_date must be a non-empty string or null")


def _normalize_action(action: Any) -> dict[str, Any]:
    if isinstance(action, str):
        normalized: dict[str, Any] = {"action": action}
    elif isinstance(action, dict):
        normalized = dict(action)
    else:
        raise OfferLabError("Action must be a string or object")
    action_name = normalized.get("action")
    if action_name not in OFFERLAB_ACTIONS:
        raise OfferLabError(f"Unsupported action {action_name!r}")
    if action_name in {"counter_at_amount", "send_seller_offer_at_amount", "reduce_listing_price", "accept"}:
        if "amount" not in normalized:
            raise OfferLabError(f"{action_name} requires amount")
        normalized["amount"] = _number(normalized["amount"])
        if normalized["amount"] <= 0:
            raise OfferLabError("action amount must be positive")
    else:
        normalized.pop("amount", None)
    return normalized


def _historical_records(data_dir: str | Path | None) -> list[dict[str, Any]]:
    if data_dir is None:
        return []
    ledger_path = Path(data_dir) / "ledger.jsonl"
    if not ledger_path.exists():
        return []
    ledger = ImmutableLedger(ledger_path)
    ledger.verify_hash_chain()
    return ledger.payloads(OFFERLAB_LEDGER_RECORD_TYPE)


def _abstention_reasons(
    snapshot: dict[str, Any],
    *,
    data_dir: str | Path | None,
    historical_records: list[dict[str, Any]],
    comparable_mature_records: list[dict[str, Any]],
    evaluated: list[dict[str, Any]],
    min_comparable_mature_cases: int,
) -> list[str]:
    context = snapshot["pre_decision_context"]
    reasons: list[str] = []
    if context["seller_cost_basis"] is None:
        reasons.append("missing_seller_cost_basis")
    if float(context["traffic_data_age_hours"]) > MAX_FRESH_TRAFFIC_AGE_HOURS:
        reasons.append("stale_traffic_data")
    if data_dir is None:
        reasons.append("no_historical_ledger")
    elif not historical_records:
        reasons.append("empty_historical_ledger")
    if len(comparable_mature_records) < min_comparable_mature_cases:
        reasons.append("insufficient_comparable_mature_outcomes")
    viable = [item for item in evaluated if not item["violates_floor"] and item["expected_contribution_margin"] is not None]
    if not viable:
        reasons.append("no_viable_actions_above_floor")
    if len(viable) >= 2:
        ordered = sorted(viable, key=lambda item: float(item["expected_contribution_margin"]), reverse=True)
        if float(ordered[0]["expected_contribution_margin"]) - float(ordered[1]["expected_contribution_margin"]) < 5.0:
            reasons.append("top_actions_too_close_for_confident_choice")
    return reasons


def _comparable_mature_records(records: list[dict[str, Any]], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    context = snapshot["pre_decision_context"]
    category = str(context["category"]).casefold()
    channel = str(context["decision_channel"])
    comparable = []
    for record in records:
        record_context = record.get("pre_decision_context")
        outcome = record.get("protected_outcome")
        if not isinstance(record_context, dict) or not isinstance(outcome, dict):
            continue
        if str(record_context.get("decision_channel")) != channel:
            continue
        if str(record_context.get("category", "")).casefold() != category:
            continue
        if record_context.get("seller_cost_basis") is None:
            continue
        if not _mature_paid_outcome(outcome):
            continue
        if outcome.get("actual_ebay_fees") is None:
            continue
        comparable.append(record)
    return comparable


def _evaluate_action(action: dict[str, Any], context: dict[str, Any], config: EconomicConfig) -> dict[str, Any]:
    action_name = str(action["action"])
    amount = _action_sale_amount(action, context)
    probability = _sale_probability(action, context)
    expected_days = _expected_days_to_sale(action, context, config)
    margin = _net_margin(amount, context, config) if amount is not None and context["seller_cost_basis"] is not None else None
    expected_margin = None
    if margin is not None:
        expected_margin = probability * margin
        expected_margin -= expected_days * config.holding_cost_per_day
        expected_margin -= probability * max(margin, 0.0) * config.return_risk_cost_rate
    net_proceeds = _net_proceeds(amount, context, config) if amount is not None else None
    violates_floor = bool(net_proceeds is not None and net_proceeds < _number(context["minimum_net_proceeds"]))
    return {
        "action": action_name,
        "amount": round(float(amount), 2) if amount is not None else None,
        "estimated_sale_probability": round(probability, 4),
        "expected_contribution_margin": round(expected_margin, 2) if expected_margin is not None else None,
        "estimated_net_margin_if_sold": round(margin, 2) if margin is not None else None,
        "expected_extra_holding_days": round(expected_days, 2),
        "violates_floor": violates_floor,
    }


def _action_sale_amount(action: dict[str, Any], context: dict[str, Any]) -> float | None:
    action_name = action["action"]
    if action_name == "decline":
        return None
    if action_name == "wait":
        band = context.get("comparable_price_band", {})
        return float(band.get("median") or context["asking_price"])
    if action_name == "accept":
        return float(action.get("amount") or context.get("buyer_offer_amount") or 0.0)
    return float(action["amount"])


def _sale_probability(action: dict[str, Any], context: dict[str, Any]) -> float:
    action_name = str(action["action"])
    views = max(int(context["views_7d"]), 0)
    trend = str(context["traffic_trend"])
    trend_adjustment = {"declining": -0.06, "flat": 0.0, "increasing": 0.06, "unknown": -0.02}[trend]
    urgency_adjustment = 0.03 * int(context["seller_urgency"])
    traffic_adjustment = min(0.12, math.log1p(views) / 60)
    if action_name == "accept":
        return 1.0
    if action_name == "decline":
        return 0.0
    if action_name == "wait":
        interested = int(context["eligible_interested_buyers"])
        return _clip(0.12 + traffic_adjustment + 0.03 * interested + trend_adjustment - urgency_adjustment, 0.02, 0.65)
    amount = float(action["amount"])
    asking = float(context["asking_price"])
    buyer_offer = context.get("buyer_offer_amount")
    if buyer_offer is None:
        discount = max(0.0, 1.0 - amount / asking)
        interested = int(context["eligible_interested_buyers"])
        return _clip(0.10 + discount * 1.8 + 0.04 * interested + traffic_adjustment + trend_adjustment, 0.02, 0.88)
    gap = max(amount - float(buyer_offer), 0.0) / max(float(buyer_offer), 1.0)
    relative_to_asking = amount / asking
    base = 0.78 - 1.85 * gap - 0.25 * max(relative_to_asking - 0.85, 0.0)
    base += traffic_adjustment + trend_adjustment + urgency_adjustment
    return _clip(base, 0.02, 0.95)


def _expected_days_to_sale(action: dict[str, Any], context: dict[str, Any], config: EconomicConfig) -> float:
    action_name = str(action["action"])
    if action_name == "accept":
        return 0.1
    if action_name == "decline":
        return float(config.horizon_days)
    if action_name == "wait":
        return min(float(config.horizon_days), max(1.0, 7.0 - int(context["seller_urgency"])))
    probability = _sale_probability(action, context)
    return _clip((1.0 - probability) * config.horizon_days, 0.5, float(config.horizon_days))


def _net_proceeds(amount: float | None, context: dict[str, Any], config: EconomicConfig) -> float:
    if amount is None:
        return 0.0
    return float(amount) * (1.0 - config.marketplace_fee_rate) - float(context["shipping_cost"]) - float(context["promotion_cost"])


def _net_margin(amount: float | None, context: dict[str, Any], config: EconomicConfig) -> float:
    if context["seller_cost_basis"] is None:
        raise OfferLabError("seller_cost_basis is required for margin estimates")
    return _net_proceeds(amount, context, config) - float(context["seller_cost_basis"])


def _summarize_action_records(items: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = [item["protected_outcome"] for item in items]
    mature_margins = [_mature_margin(outcome) for outcome in outcomes]
    mature_margins = [value for value in mature_margins if value is not None]
    provisional_margins = [_provisional_margin(outcome) for outcome in outcomes]
    provisional_margins = [value for value in provisional_margins if value is not None]
    days = [max(float(outcome.get("days_to_sale") or 1.0), 1.0) for outcome in outcomes]
    mature_per_day = []
    for outcome, day in zip(outcomes, days, strict=True):
        margin = _mature_margin(outcome)
        if margin is not None:
            mature_per_day.append(margin / day)
    return {
        "decisions": len(items),
        "buyer_paid_rate": _rate(sum(1 for outcome in outcomes if _buyer_paid(outcome)), len(outcomes)),
        "sold_within_7_days_rate": _rate(sum(1 for outcome in outcomes if outcome["sold_within_7_days"] and _buyer_paid(outcome)), len(outcomes)),
        "seller_accepted_unpaid": sum(1 for outcome in outcomes if outcome["seller_accepted"] and not outcome["buyer_paid"]),
        "mature_decisions": len(mature_margins),
        "immature_or_provisional_decisions": len(items) - len(mature_margins),
        "total_mature_contribution_margin": round(sum(mature_margins), 2),
        "average_mature_contribution_margin": round(sum(mature_margins) / len(mature_margins), 2) if mature_margins else None,
        "average_mature_margin_per_listing_day": round(sum(mature_per_day) / len(mature_per_day), 2) if mature_per_day else None,
        "total_provisional_contribution_margin": round(sum(provisional_margins), 2),
    }


def _group_summary(records: list[dict[str, Any]], key_fn: Any) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(key_fn(record))].append(record)
    return {name: _summarize_action_records(items) for name, items in sorted(grouped.items())}


def _historical_policy_audit(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "offer_ratio_buckets": _bucket_rows(records, _offer_ratio_bucket),
        "category_breakout": _bucket_rows(records, lambda record: str(record["pre_decision_context"]["category"])),
        "listing_age_breakout": _bucket_rows(records, lambda record: _age_bucket(int(record["pre_decision_context"]["days_active"]))),
        "cost_band_breakout": _bucket_rows(records, _cost_bucket),
        "traffic_breakout": _bucket_rows(records, lambda record: str(record["pre_decision_context"]["traffic_trend"])),
        "interested_buyer_breakout": _bucket_rows(records, lambda record: _interested_bucket(int(record["pre_decision_context"]["eligible_interested_buyers"]))),
        "prior_offer_breakout": _bucket_rows(records, lambda record: _prior_offer_bucket(int(record["pre_decision_context"]["prior_offer_count"]))),
        "seller_urgency_breakout": _bucket_rows(records, lambda record: str(record["pre_decision_context"]["seller_urgency"])),
    }


def _bucket_rows(records: list[dict[str, Any]], bucket_fn: Any) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(bucket_fn(record))].append(record)
    rows = []
    for bucket, items in sorted(grouped.items()):
        outcomes = [item["protected_outcome"] for item in items]
        action_counts: dict[str, int] = defaultdict(int)
        for item in items:
            action_counts[_normalize_action(item["action_taken"])["action"]] += 1
        rows.append(
            {
                "bucket": bucket,
                "decisions": len(items),
                "action_mix": dict(sorted(action_counts.items())),
                "buyer_paid_rate": _rate(sum(1 for outcome in outcomes if _buyer_paid(outcome)), len(outcomes)),
                "mature_paid_outcomes": sum(1 for outcome in outcomes if _mature_paid_outcome(outcome)),
                "average_mature_margin": _average([_mature_margin(outcome) for outcome in outcomes]),
            }
        )
    return rows


def _profit_frontier(audit: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for action, summary in sorted(audit["by_action"].items()):
        rows.append(
            {
                "action": action,
                "decisions": summary["decisions"],
                "buyer_paid_rate": summary["buyer_paid_rate"],
                "mature_decisions": summary["mature_decisions"],
                "average_mature_margin": summary["average_mature_contribution_margin"],
                "average_mature_margin_per_listing_day": summary["average_mature_margin_per_listing_day"],
            }
        )
    return rows


def _missed_opportunities(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for record in records:
        context = record["pre_decision_context"]
        outcome = record["protected_outcome"]
        action = _normalize_action(record["action_taken"])["action"]
        mature_margin = _mature_margin(outcome)
        buyer_offer = context.get("buyer_offer_amount")
        if action in {"decline", "wait"} and buyer_offer is not None and mature_margin is not None and mature_margin <= 0:
            candidates.append(
                {
                    "decision_id": record["decision_id"],
                    "reason": "retrospective_positive_offer_was_not_closed",
                    "buyer_offer_amount": buyer_offer,
                    "minimum_net_proceeds": context["minimum_net_proceeds"],
                }
            )
        if action == "accept" and outcome["returned"]:
            candidates.append(
                {
                    "decision_id": record["decision_id"],
                    "reason": "accepted_order_later_returned",
                    "mature_margin": mature_margin,
                    "note": "audit only; not evidence that accepting caused the return",
                }
            )
        if len(candidates) >= 10:
            break
    return candidates


def _proposed_policy(audit: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    quality = audit["data_quality"]
    if quality["score"] < 0.8 or quality["mature_paid_outcomes"] < 30:
        return {
            "status": "insufficient_evidence",
            "rules": [
                "Do not deploy an optimized policy yet.",
                "Use the audit to identify missing cost basis, fee, and return-window data.",
                "Collect more mature paid outcomes before recommending seller action changes.",
            ],
            "guardrails": [
                "Never recommend below minimum_net_proceeds.",
                "Never pool decision channels.",
                "Treat all retrospective lift as a hypothesis.",
            ],
        }
    channels = sorted({str(record["pre_decision_context"]["decision_channel"]) for record in records})
    return {
        "status": "candidate_for_preregistration",
        "rules": [
            "For buyer-initiated Best Offers, compare accept-now against one counter amount above buyer offer and below comparable median.",
            "For seller-initiated offers and price reductions, test only listing-level policies.",
            f"Keep channels separated: {', '.join(channels)}.",
        ],
        "guardrails": [
            "Seller approves every action.",
            "Never cross minimum_net_proceeds.",
            "Use mature contribution margin per listing-day as the primary metric.",
        ],
    }


def _prospective_test_plan(records: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = sum(1 for record in records if record.get("protected_outcome") is None)
    return {
        "status": "not_started",
        "eligible_current_snapshots": eligible,
        "design": [
            "Run a preregistered listing-level randomized policy test only after the read-only audit is complete.",
            "Control: seller's current policy.",
            "Treatment: one explicit counter/discount policy generated from the audit and approved by the seller.",
            "Stratify by category, price band, listing age, traffic trend, and prior offer count.",
            "Primary metric: mature contribution margin per listing-day.",
        ],
        "guardrails": [
            "Seller approves actions during decision-support stage.",
            "Track assignment probability and adherence.",
            "Do not score profit before fees and return-window maturity are known.",
        ],
    }


def _data_quality(records: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = [record["protected_outcome"] for record in records if isinstance(record.get("protected_outcome"), dict)]
    cost_basis_known = sum(1 for record in records if record.get("pre_decision_context", {}).get("seller_cost_basis") is not None)
    fee_known = sum(1 for outcome in outcomes if outcome.get("actual_ebay_fees") is not None)
    mature_return = sum(1 for outcome in outcomes if bool(outcome.get("return_window_matured")))
    mature_paid = sum(1 for outcome in outcomes if _mature_paid_outcome(outcome))
    fresh_traffic = sum(
        1
        for record in records
        if float(record.get("pre_decision_context", {}).get("traffic_data_age_hours", math.inf)) <= MAX_FRESH_TRAFFIC_AGE_HOURS
    )
    cost_coverage = _rate(cost_basis_known, len(records))
    fee_coverage = _rate(fee_known, len(outcomes))
    mature_coverage = _rate(mature_return, len(outcomes))
    traffic_coverage = _rate(fresh_traffic, len(records))
    score = round((cost_coverage + fee_coverage + mature_coverage + traffic_coverage) / 4, 3) if records else 0.0
    warnings = []
    if cost_coverage < 0.95:
        warnings.append("seller cost basis is incomplete")
    if fee_coverage < 0.95:
        warnings.append("actual eBay fee coverage is incomplete")
    if mature_coverage < 0.8:
        warnings.append("return windows are not mature enough for confident profit scoring")
    if mature_paid < 30:
        warnings.append("fewer than 30 mature paid outcomes")
    if traffic_coverage < 0.95:
        warnings.append("some traffic observations are stale")
    return {
        "score": score,
        "snapshots": len(records),
        "decisions_with_outcomes": len(outcomes),
        "cost_basis_known": cost_basis_known,
        "cost_basis_coverage": cost_coverage,
        "actual_ebay_fee_known": fee_known,
        "actual_ebay_fee_coverage": fee_coverage,
        "mature_return_window_count": mature_return,
        "mature_return_window_coverage": mature_coverage,
        "fresh_traffic_count": fresh_traffic,
        "fresh_traffic_coverage": traffic_coverage,
        "mature_paid_outcomes": mature_paid,
        "warnings": warnings,
    }


def _offer_ratio_bucket(record: dict[str, Any]) -> str:
    ratio = record["pre_decision_context"].get("offer_to_asking_ratio")
    if ratio is None:
        return "no_buyer_offer"
    ratio = float(ratio)
    if ratio < 0.65:
        return "below_65_pct"
    if ratio < 0.75:
        return "65_to_75_pct"
    if ratio < 0.85:
        return "75_to_85_pct"
    return "above_85_pct"


def _age_bucket(days_active: int) -> str:
    if days_active < 14:
        return "0_to_13_days"
    if days_active < 45:
        return "14_to_44_days"
    if days_active < 90:
        return "45_to_89_days"
    return "90_plus_days"


def _cost_bucket(record: dict[str, Any]) -> str:
    cost = record["pre_decision_context"].get("seller_cost_basis")
    if cost is None:
        return "missing_cost_basis"
    cost = float(cost)
    if cost < 100:
        return "under_100"
    if cost < 500:
        return "100_to_499"
    if cost < 1000:
        return "500_to_999"
    return "1000_plus"


def _interested_bucket(value: int) -> str:
    if value == 0:
        return "0"
    if value <= 2:
        return "1_to_2"
    if value <= 5:
        return "3_to_5"
    return "6_plus"


def _prior_offer_bucket(value: int) -> str:
    if value == 0:
        return "0"
    if value <= 2:
        return "1_to_2"
    return "3_plus"


def _buyer_paid(outcome: dict[str, Any]) -> bool:
    return bool(outcome.get("buyer_paid")) and not bool(outcome.get("order_cancelled")) and not bool(outcome.get("unpaid_order"))


def _mature_paid_outcome(outcome: dict[str, Any]) -> bool:
    return _buyer_paid(outcome) and bool(outcome.get("return_window_matured")) and outcome.get("mature_margin") is not None


def _mature_margin(outcome: dict[str, Any]) -> float | None:
    if not _mature_paid_outcome(outcome):
        return None
    return float(outcome["mature_margin"])


def _provisional_margin(outcome: dict[str, Any]) -> float | None:
    value = outcome.get("provisional_margin")
    if value is None:
        return None
    return float(value)


def _confidence_label(comparable_mature_cases: int, evaluated: list[dict[str, Any]]) -> str:
    if comparable_mature_cases >= 100:
        return "moderate"
    if comparable_mature_cases >= 30:
        return "low-moderate"
    if any(action["violates_floor"] for action in evaluated):
        return "low"
    return "low"


def _ensure_offerlab_definition(ledger: ImmutableLedger) -> None:
    existing = ledger.find_record(OFFERLAB_CAMPAIGN_RECORD_ID, "campaign_definition")
    definition = campaign_002_definition()
    if existing is not None:
        if existing.get("payload") != definition:
            raise OfferLabError("Existing Campaign 002 definition differs from current definition")
        return
    ledger.append("campaign_definition", definition, record_id=OFFERLAB_CAMPAIGN_RECORD_ID, unique_record_id=True)


def _markdown_table(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["No rows."]
    columns = list(rows[0].keys())
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_cell(row.get(column)) for column in columns) + " |")
    return lines


def _cell(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{key}: {item}" for key, item in value.items())
    if value is None:
        return ""
    return str(value)


def _average(values: list[float | None]) -> float | None:
    concrete = [value for value in values if value is not None]
    if not concrete:
        return None
    return round(sum(concrete) / len(concrete), 2)


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OfferLabError("Expected a numeric value")
    if not math.isfinite(float(value)):
        raise OfferLabError("Numeric values must be finite")
    return float(value)


def _nonnegative_int(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OfferLabError(f"{key} must be a non-negative integer")
    return value


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
