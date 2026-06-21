from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any, Protocol

from behavior_lab.core import parse_time, stable_hash
from behavior_lab.ledger import ImmutableLedger
from behavior_lab.temporal import assert_feature_map_is_pre_decision


OFFERLAB_CAMPAIGN_ID = "campaign_002_ebay_seller_offers"
OFFERLAB_SCHEMA_VERSION = "offerlab_decision_snapshot.v1"
OFFERLAB_CAMPAIGN_SCHEMA_VERSION = "0.1"
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

TRAFFIC_TRENDS = {"declining", "flat", "increasing", "unknown"}
POST_DECISION_OUTCOME_KEYS = {
    "offer_accepted",
    "countered",
    "ignored",
    "sold_within_48_hours",
    "sold_within_7_days",
    "final_sale_price",
    "net_contribution_margin",
    "days_to_sale",
    "unpaid_order",
    "returned",
}


class OfferLabError(ValueError):
    pass


class EbayReadOnlyAdapter(Protocol):
    """Boundary for a future official eBay API adapter.

    Implementations may read active listings, offers, traffic, and completed
    sales. They must not accept, decline, counter, discount, or otherwise mutate
    marketplace state.
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
        "working_names": ["OfferLab", "MarginPilot"],
        "commercial_goal": "Optimize seller-side offer and pricing decisions for realized contribution margin.",
        "stage": "read_only_profit_audit",
        "primary_metric": "net_contribution_margin_per_listing_day",
        "pre_decision_context": {
            "listing_id": "string",
            "category": "string",
            "condition": "string",
            "asking_price": "positive number",
            "seller_cost_basis": "non-negative number",
            "minimum_net_proceeds": "non-negative number",
            "days_active": "non-negative integer",
            "impressions_7d": "non-negative integer",
            "views_7d": "non-negative integer",
            "traffic_trend": "declining | flat | increasing | unknown",
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
            "category": "refurbished electronics",
            "condition": "used",
            "asking_price": 900.0,
            "seller_cost_basis": 520.0,
            "minimum_net_proceeds": 580.0,
            "days_active": 26,
            "impressions_7d": 910,
            "views_7d": 81,
            "traffic_trend": "declining",
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
    assert_feature_map_is_pre_decision(context, target_name="net_contribution_margin")

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
        margins = [float(item["protected_outcome"].get("net_contribution_margin") or 0.0) for item in items]
        sold_7d = [bool(item["protected_outcome"].get("sold_within_7_days")) for item in items]
        days = [max(float(item["protected_outcome"].get("days_to_sale") or 0.0), 1.0) for item in items]
        by_action[action_name] = {
            "decisions": len(items),
            "sold_within_7_days_rate": sum(1 for value in sold_7d if value) / len(sold_7d),
            "total_net_contribution_margin": round(sum(margins), 2),
            "average_net_contribution_margin": round(sum(margins) / len(margins), 2),
            "average_margin_per_listing_day": round(sum(margin / day for margin, day in zip(margins, days, strict=True)) / len(items), 2),
        }
    total_margin = sum(float(item["protected_outcome"].get("net_contribution_margin") or 0.0) for item in with_outcomes)
    ledger.verify_hash_chain()
    return {
        "campaign_id": OFFERLAB_CAMPAIGN_ID,
        "ledger": str(ledger.path.resolve()),
        "ledger_valid": True,
        "decisions": len(records),
        "decisions_with_outcomes": len(with_outcomes),
        "total_net_contribution_margin": round(total_margin, 2),
        "by_action": by_action,
        "primary_metric": "net_contribution_margin_per_listing_day",
    }


def recommend_offer_action(
    snapshot: dict[str, Any],
    *,
    data_dir: str | Path | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = validate_offer_decision_snapshot(dict(snapshot))
    economic = EconomicConfig.from_mapping(config)
    historical_cases = 0
    if data_dir is not None:
        ledger_path = Path(data_dir) / "ledger.jsonl"
        if ledger_path.exists():
            historical_cases = len(ImmutableLedger(ledger_path).payloads(OFFERLAB_LEDGER_RECORD_TYPE))
    evaluated = [
        _evaluate_action(_normalize_action(action), snapshot["pre_decision_context"], economic)
        for action in snapshot["available_actions"]
    ]
    viable = [item for item in evaluated if not item["violates_floor"]]
    best = max(viable or evaluated, key=lambda item: item["expected_contribution_margin"])
    accept = next((item for item in evaluated if item["action"] == "accept"), None)
    advantage = None
    if accept is not None:
        advantage = round(best["expected_contribution_margin"] - accept["expected_contribution_margin"], 2)
    return {
        "campaign_id": OFFERLAB_CAMPAIGN_ID,
        "decision_id": snapshot["decision_id"],
        "recommendation": {
            "action": best["action"],
            "amount": best.get("amount"),
            "expected_contribution_margin": best["expected_contribution_margin"],
            "expected_advantage_over_accept_now": advantage,
            "confidence": _confidence_label(historical_cases, evaluated),
            "source": "deterministic_read_only_arithmetic_v1",
        },
        "evaluated_actions": evaluated,
        "historical_cases_considered": historical_cases,
        "execute_action": False,
        "guardrail": "read-only recommendation; seller must choose the action",
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
        "category",
        "condition",
        "asking_price",
        "seller_cost_basis",
        "minimum_net_proceeds",
        "days_active",
        "impressions_7d",
        "views_7d",
        "traffic_trend",
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
    for key in ["asking_price"]:
        if _number(context[key]) <= 0:
            raise OfferLabError(f"{key} must be positive")
    for key in ["seller_cost_basis", "minimum_net_proceeds", "shipping_cost", "promotion_cost"]:
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
    for key in ["offer_accepted", "countered", "ignored", "sold_within_48_hours", "sold_within_7_days", "unpaid_order", "returned"]:
        if not isinstance(outcome[key], bool):
            raise OfferLabError(f"{key} must be boolean")
    for key in ["final_sale_price", "net_contribution_margin", "days_to_sale"]:
        if outcome[key] is not None and not isinstance(outcome[key], (int, float)):
            raise OfferLabError(f"{key} must be numeric or null")
    if outcome["days_to_sale"] is not None and float(outcome["days_to_sale"]) < 0:
        raise OfferLabError("days_to_sale may not be negative")


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


def _evaluate_action(action: dict[str, Any], context: dict[str, Any], config: EconomicConfig) -> dict[str, Any]:
    action_name = str(action["action"])
    amount = _action_sale_amount(action, context)
    probability = _sale_probability(action, context)
    expected_days = _expected_days_to_sale(action, context, config)
    margin = _net_margin(amount, context, config) if amount is not None else 0.0
    expected_margin = probability * margin
    expected_margin -= expected_days * config.holding_cost_per_day
    expected_margin -= probability * max(margin, 0.0) * config.return_risk_cost_rate
    net_proceeds = _net_proceeds(amount, context, config) if amount is not None else None
    violates_floor = bool(net_proceeds is not None and net_proceeds < _number(context["minimum_net_proceeds"]))
    return {
        "action": action_name,
        "amount": round(float(amount), 2) if amount is not None else None,
        "estimated_sale_probability": round(probability, 4),
        "expected_contribution_margin": round(expected_margin, 2),
        "estimated_net_margin_if_sold": round(margin, 2),
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
    return _net_proceeds(amount, context, config) - float(context["seller_cost_basis"])


def _confidence_label(historical_cases: int, evaluated: list[dict[str, Any]]) -> str:
    if historical_cases >= 100:
        return "moderate"
    if historical_cases >= 30:
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
