from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any

from behavior_lab.core import parse_time, stable_hash
from behavior_lab.ledger import ImmutableLedger


MARGINPILOT_PRODUCT_ID = "marginpilot_negotiated_commerce"
MARGINPILOT_SCHEMA_VERSION = "marginpilot_event.v1"
MARGINPILOT_RECORD_TYPE = "marginpilot_event"
DEFAULT_DATA_DIR = Path(r"C:\OfferLabData\marginpilot")

EVENT_TYPES = {"merchant_consent", "offer_opened", "merchant_decision", "outcome_matured"}
SURFACES = {"product_page_offer", "cart_offer", "quote_request", "merchant_entered"}
ACTION_TYPES = {
    "accept",
    "decline",
    "counter_at_amount",
    "wait",
    "free_shipping_counter",
    "bundle_counter",
}
MERCHANT_DECISIONS = ACTION_TYPES | {"manual_other"}
PII_KEYS = {
    "buyer_name",
    "customer_name",
    "name",
    "email",
    "buyer_email",
    "customer_email",
    "phone",
    "address",
    "shipping_address",
    "billing_address",
    "ip_address",
    "customer_id",
    "buyer_id",
}
PII_CONTEXT_KEYS = {"buyer", "customer", "contact", "client", "shopper", "person", "user", "account"}
PII_IDENTIFIER_TOKENS = {"id", "gid", "handle"}
PII_CONTACT_TOKENS = {"email", "phone", "address", "ip"}
PII_TEXT_TOKENS = {"note", "notes", "message", "comment", "memo"}
PII_VALUE_PATTERNS = [
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(\d{3}\)\s*|\d{3}[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(
        r"\b\d{1,6}\s+[A-Z0-9.'-]+(?:\s+[A-Z0-9.'-]+){0,4}\s+"
        r"(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|way|court|ct|place|pl)\b",
        re.IGNORECASE,
    ),
    re.compile(r"gid://shopify/customer/", re.IGNORECASE),
]
POST_DECISION_CONTEXT_KEYS = {
    "accepted",
    "buyer_paid",
    "final_sale_price",
    "returned",
    "cancelled",
    "mature_contribution_margin",
    "refund_amount",
}
MONEY_FIELDS = {
    "asking_price",
    "buyer_offer_amount",
    "cost_basis",
    "platform_fee_rate",
    "payment_fee_flat",
    "shipping_cost",
    "fulfillment_cost",
    "return_allowance",
    "holding_cost_per_day",
    "merchant_floor_mature_margin",
    "amount",
    "final_sale_price",
    "actual_fees",
    "actual_shipping_cost",
    "actual_fulfillment_cost",
    "refund_amount",
    "mature_contribution_margin",
}


class MarginPilotError(ValueError):
    pass


@dataclass(frozen=True)
class MarginPilotIngestResult:
    product_id: str
    imported: int
    skipped_existing: int
    ledger: str
    event_hashes: list[str]


def sample_marginpilot_events() -> dict[str, dict[str, Any]]:
    now = "2026-06-22T10:00:00-04:00"
    merchant_id = "merchant_demo_refurb_tech"
    offer_id = "offer_demo_001"
    return {
        "merchant_consent": {
            "schema_version": MARGINPILOT_SCHEMA_VERSION,
            "event_type": "merchant_consent",
            "event_id": "consent_demo_001",
            "merchant_id": merchant_id,
            "occurred_at": now,
            "consent": {
                "merchant_specific_learning_authorized": True,
                "cross_merchant_pooling_authorized": False,
                "authorized_uses": ["merchant_specific_shadow_recommendations", "merchant_specific_policy_experiments"],
                "written_consent_reference": "signed_marginpilot_consent_v1",
                "consent_text_hash": stable_hash(
                    {
                        "scope": "merchant-specific learning only",
                        "pii": "excluded from model features",
                    }
                ),
                "pii_exclusion_acknowledged": True,
                "deletion_support_acknowledged": True,
            },
            "provenance": {"source": "manual_template"},
        },
        "offer_opened": {
            "schema_version": MARGINPILOT_SCHEMA_VERSION,
            "event_type": "offer_opened",
            "event_id": offer_id,
            "merchant_id": merchant_id,
            "offer_id": offer_id,
            "listing_id": "sku_refurb_pc_001",
            "occurred_at": now,
            "observation_cutoff": now,
            "surface": "product_page_offer",
            "pre_decision_context": {
                "listing_id": "sku_refurb_pc_001",
                "sku": "refurb-pc-i7-16gb",
                "category": "refurbished technology",
                "condition": "refurbished",
                "currency": "USD",
                "asking_price": 900.0,
                "buyer_offer_amount": 720.0,
                "cost_basis": 520.0,
                "platform_fee_rate": 0.029,
                "payment_fee_flat": 0.3,
                "shipping_cost": 34.0,
                "fulfillment_cost": 12.0,
                "return_allowance": 18.0,
                "inventory_age_days": 31,
                "holding_cost_per_day": 1.25,
                "merchant_floor_mature_margin": 75.0,
                "quantity_available": 1,
                "comparable_inventory_key": "refurb-pc-i7",
            },
            "available_actions": [
                {"action": "accept", "amount": 720.0},
                {"action": "counter_at_amount", "amount": 760.0},
                {"action": "counter_at_amount", "amount": 790.0},
                {"action": "wait"},
                {"action": "decline"},
            ],
            "provenance": {"source": "manual_template"},
        },
        "merchant_decision": {
            "schema_version": MARGINPILOT_SCHEMA_VERSION,
            "event_type": "merchant_decision",
            "event_id": "decision_demo_001",
            "merchant_id": merchant_id,
            "offer_id": offer_id,
            "occurred_at": "2026-06-22T10:05:00-04:00",
            "selected_action": {"action": "counter_at_amount", "amount": 760.0},
            "assignment": {
                "policy_id": "merchant_manual_policy",
                "assignment_probability": 1.0,
                "randomized": False,
            },
            "provenance": {"source": "manual_template"},
        },
        "outcome_matured": {
            "schema_version": MARGINPILOT_SCHEMA_VERSION,
            "event_type": "outcome_matured",
            "event_id": "outcome_demo_001",
            "merchant_id": merchant_id,
            "offer_id": offer_id,
            "order_id": "order_demo_001",
            "occurred_at": "2026-07-22T10:00:00-04:00",
            "outcome": {
                "buyer_paid": True,
                "returned": False,
                "cancelled": False,
                "final_sale_price": 760.0,
                "actual_fees": 22.34,
                "actual_shipping_cost": 34.0,
                "actual_fulfillment_cost": 12.0,
                "cost_basis": 520.0,
                "refund_amount": 0.0,
                "mature_contribution_margin": 171.66,
                "inventory_days_until_sale": 4.3,
                "return_window_matured": True,
            },
            "provenance": {"source": "manual_template"},
        },
    }


def write_marginpilot_templates(output_dir: str | Path) -> dict[str, Any]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    events = sample_marginpilot_events()
    for name, event in events.items():
        (destination / f"{name}.json").write_text(json.dumps(event, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "marginpilot_templates.v1",
        "product_id": MARGINPILOT_PRODUCT_ID,
        "output_dir": str(destination.resolve()),
        "events": {name: f"{name}.json" for name in sorted(events)},
        "data_rights": {
            "merchant_specific_learning_requires_written_consent": True,
            "cross_merchant_pooling_default": False,
            "customer_pii_allowed_in_model_features": False,
        },
        "month_1_scope": [
            "offer and quote event capture",
            "merchant inbox accounting",
            "mature outcome ledger",
            "explicit merchant-specific learning consent",
            "no automatic negotiation",
        ],
    }
    (destination / "marginpilot_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def load_marginpilot_events(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if not text.strip():
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if payload is None:
        events = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MarginPilotError(f"Invalid JSONL at line {line_number}") from exc
            if not isinstance(item, dict):
                raise MarginPilotError(f"Event at line {line_number} must be an object")
            events.append(item)
        return events
    if isinstance(payload, dict) and isinstance(payload.get("events"), list):
        events = payload["events"]
    elif isinstance(payload, list):
        events = payload
    elif isinstance(payload, dict):
        events = [payload]
    else:
        raise MarginPilotError("Expected JSON object, array, envelope, or JSONL")
    if not all(isinstance(item, dict) for item in events):
        raise MarginPilotError("All events must be JSON objects")
    return list(events)


def ingest_marginpilot_events(path: str | Path, *, data_dir: str | Path = DEFAULT_DATA_DIR) -> MarginPilotIngestResult:
    ledger = ImmutableLedger(Path(data_dir) / "ledger.jsonl")
    imported = 0
    skipped = 0
    hashes: list[str] = []
    for raw in load_marginpilot_events(path):
        event = with_event_hash(validate_marginpilot_event(dict(raw)))
        record_id = f"marginpilot_{event['merchant_id']}_{event['event_type']}_{event['event_id']}"
        existing = ledger.find_record(record_id, MARGINPILOT_RECORD_TYPE)
        if existing is not None:
            if existing.get("payload") != event:
                raise MarginPilotError(f"Existing event {record_id!r} differs from imported event")
            skipped += 1
            hashes.append(str(event["event_hash"]))
            continue
        ledger.append(MARGINPILOT_RECORD_TYPE, event, record_id=record_id, unique_record_id=True)
        imported += 1
        hashes.append(str(event["event_hash"]))
    ledger.verify_hash_chain()
    return MarginPilotIngestResult(
        product_id=MARGINPILOT_PRODUCT_ID,
        imported=imported,
        skipped_existing=skipped,
        ledger=str(ledger.path.resolve()),
        event_hashes=hashes,
    )


def validate_marginpilot_event(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("schema_version") != MARGINPILOT_SCHEMA_VERSION:
        raise MarginPilotError(f"schema_version must be {MARGINPILOT_SCHEMA_VERSION!r}")
    event_type = event.get("event_type")
    if event_type not in EVENT_TYPES:
        raise MarginPilotError(f"event_type must be one of {sorted(EVENT_TYPES)}")
    for key in ["event_id", "merchant_id", "occurred_at"]:
        if not isinstance(event.get(key), str) or not str(event[key]).strip():
            raise MarginPilotError(f"{key} must be a non-empty string")
    parse_time(str(event["occurred_at"]))
    _reject_pii(event)
    if event_type == "merchant_consent":
        _validate_consent(event)
    elif event_type == "offer_opened":
        _validate_offer_opened(event)
    elif event_type == "merchant_decision":
        _validate_merchant_decision(event)
    elif event_type == "outcome_matured":
        _validate_outcome(event)
    provenance = event.get("provenance")
    if not isinstance(provenance, dict):
        raise MarginPilotError("provenance must be an object")
    return event


def event_hash(event: dict[str, Any]) -> str:
    body = dict(event)
    body.pop("event_hash", None)
    return stable_hash(body)


def with_event_hash(event: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(event)
    prepared["event_hash"] = event_hash(prepared)
    return prepared


def marginpilot_inbox(data_dir: str | Path = DEFAULT_DATA_DIR, *, merchant_id: str | None = None) -> dict[str, Any]:
    events = _events(data_dir, merchant_id=merchant_id)
    opened = [event for event in events if event["event_type"] == "offer_opened"]
    decided_offer_ids = {_merchant_offer_key(event) for event in events if event["event_type"] == "merchant_decision"}
    matured_offer_ids = {_merchant_offer_key(event) for event in events if event["event_type"] == "outcome_matured"}
    consent_by_merchant = _latest_consent_by_merchant(events)
    rows = []
    for event in opened:
        key = _merchant_offer_key(event)
        if key in decided_offer_ids or key in matured_offer_ids:
            continue
        rows.append(
            {
                "offer_id": str(event["offer_id"]),
                "merchant_id": event["merchant_id"],
                "listing_id": event["listing_id"],
                "surface": event["surface"],
                "opened_at": event["occurred_at"],
                "economics": _action_economics(event),
                "system_mode": "accounting_only",
                "automation_allowed": False,
                "merchant_specific_learning_authorized": _consent_authorizes_learning(consent_by_merchant.get(str(event["merchant_id"]))),
            }
        )
    return {
        "schema_version": "marginpilot_inbox.v1",
        "product_id": MARGINPILOT_PRODUCT_ID,
        "merchant_id": merchant_id,
        "ledger": str((Path(data_dir) / "ledger.jsonl").resolve()),
        "open_offers": rows,
        "open_offer_count": len(rows),
        "executes_seller_actions": False,
    }


def marginpilot_audit(data_dir: str | Path = DEFAULT_DATA_DIR, *, merchant_id: str | None = None) -> dict[str, Any]:
    events = _events(data_dir, merchant_id=merchant_id)
    offers = [event for event in events if event["event_type"] == "offer_opened"]
    decisions = [event for event in events if event["event_type"] == "merchant_decision"]
    outcomes = [event for event in events if event["event_type"] == "outcome_matured"]
    merchant_ids = sorted({str(event["merchant_id"]) for event in events})
    single_merchant_namespace = len(merchant_ids) <= 1
    consent = _latest_consent(events) if single_merchant_namespace else None
    integrity = _thread_integrity(events)
    cost_known = sum(1 for event in offers if event["pre_decision_context"].get("cost_basis") is not None)
    mature_margins = [float(event["outcome"]["mature_contribution_margin"]) for event in outcomes if _mature_paid(event["outcome"])]
    total_margin = round(sum(mature_margins), 2)
    by_surface: dict[str, int] = defaultdict(int)
    for event in offers:
        by_surface[str(event["surface"])] += 1
    consent_authorized = _consent_authorizes_learning(consent)
    cost_coverage = _rate(cost_known, len(offers))
    checks = {
        "single_merchant_namespace": single_merchant_namespace,
        "merchant_specific_learning_consent": consent_authorized,
        "cost_basis_coverage": cost_coverage >= 0.8,
        "minimum_mature_outcomes": len(mature_margins) >= 30,
        "event_thread_integrity": integrity["passed"],
        "no_customer_pii_detected": _payloads_have_no_pii(events),
    }
    return {
        "schema_version": "marginpilot_audit.v1",
        "product_id": MARGINPILOT_PRODUCT_ID,
        "merchant_id": merchant_id,
        "merchant_namespaces": merchant_ids,
        "ledger": str((Path(data_dir) / "ledger.jsonl").resolve()),
        "ledger_valid": ImmutableLedger(Path(data_dir) / "ledger.jsonl").verify_hash_chain(),
        "counts": {
            "events": len(events),
            "offers_opened": len(offers),
            "merchant_decisions": len(decisions),
            "mature_outcomes": len(outcomes),
            "mature_paid_outcomes": len(mature_margins),
        },
        "by_surface": dict(sorted(by_surface.items())),
        "mature_contribution_margin": {
            "total": total_margin,
            "average": round(total_margin / len(mature_margins), 2) if mature_margins else None,
        },
        "coverage": {
            "cost_basis": cost_coverage,
        },
        "data_rights": {
            "merchant_specific_learning_authorized": consent_authorized,
            "cross_merchant_pooling_authorized": bool((consent or {}).get("consent", {}).get("cross_merchant_pooling_authorized")),
            "cross_merchant_pooling_default": False,
            "customer_pii_allowed_in_model_features": False,
        },
        "profit_optimization_gate": {
            "passed": all(checks.values()),
            "checks": checks,
            "event_thread_integrity": integrity,
            "thresholds": {
                "minimum_cost_basis_coverage": 0.8,
                "minimum_mature_paid_outcomes": 30,
            },
        },
        "current_stage": "transaction_surface" if not all(checks.values()) else "ready_for_shadow_optimizer",
        "model_training": "not_run",
        "automation_allowed": False,
    }


def marginpilot_utility_report(data_dir: str | Path = DEFAULT_DATA_DIR, *, merchant_id: str | None = None) -> dict[str, Any]:
    events = _events(data_dir, merchant_id=merchant_id)
    threads = _offer_threads(events)
    decided_threads = [thread for thread in threads if thread["decision"] is not None]
    matured_threads = [thread for thread in threads if thread["outcome"] is not None]
    paid_threads = [thread for thread in matured_threads if _mature_paid(thread["outcome"]["outcome"])]
    nonreturned_paid_threads = [thread for thread in paid_threads if not bool(thread["outcome"]["outcome"].get("returned"))]
    accepted_threads = [thread for thread in decided_threads if _is_accepting_decision(thread["decision"])]
    unpaid_accepted = [thread for thread in accepted_threads if not _thread_has_paid_outcome(thread)]
    accepted_paid_threads = [thread for thread in accepted_threads if thread["outcome"] is not None and _mature_paid(thread["outcome"]["outcome"])]
    mature_margins = [float(thread["outcome"]["outcome"]["mature_contribution_margin"]) for thread in paid_threads]
    accepted_mature_margins = [float(thread["outcome"]["outcome"]["mature_contribution_margin"]) for thread in accepted_paid_threads]
    final_sales = [float(thread["outcome"]["outcome"].get("final_sale_price") or 0.0) for thread in paid_threads]
    accepted_final_sales = [float(thread["outcome"]["outcome"].get("final_sale_price") or 0.0) for thread in accepted_paid_threads]
    refunds = [float(thread["outcome"]["outcome"].get("refund_amount") or 0.0) for thread in matured_threads]
    accepted_refunds = [float(thread["outcome"]["outcome"].get("refund_amount") or 0.0) for thread in accepted_paid_threads]
    concessions = [_concession_row(thread) for thread in decided_threads]
    concessions = [row for row in concessions if row is not None]
    return {
        "schema_version": "marginpilot_merchant_utility_report.v1",
        "product_id": MARGINPILOT_PRODUCT_ID,
        "merchant_id": merchant_id,
        "ledger": str((Path(data_dir) / "ledger.jsonl").resolve()),
        "model_training": "not_run",
        "automation_allowed": False,
        "causal_claim": False,
        "offer_volume_and_acceptance_funnel": {
            "offers_opened": len(threads),
            "merchant_responded": len(decided_threads),
            "accepted_or_countered": len(accepted_threads),
            "mature_outcomes": len(matured_threads),
            "paid_mature_outcomes": len(paid_threads),
            "paid_nonreturned_mature_outcomes": len(nonreturned_paid_threads),
            "returned": sum(1 for thread in matured_threads if bool(thread["outcome"]["outcome"].get("returned"))),
            "cancelled": sum(1 for thread in matured_threads if bool(thread["outcome"]["outcome"].get("cancelled"))),
        },
        "mature_margin_per_accepted_offer": [_mature_margin_row(thread) for thread in accepted_paid_threads],
        "margin_by_product_and_inventory_age": _margin_by_product_and_age(paid_threads),
        "amount_conceded_vs_asking": {
            "rows": concessions,
            "average_concession": _mean([row["amount_conceded"] for row in concessions]),
            "average_concession_rate": _mean([row["concession_rate"] for row in concessions]),
        },
        "time_from_offer_to_payment": _time_to_payment_summary(paid_threads),
        "unpaid_accepted_offers": [_unpaid_row(thread) for thread in unpaid_accepted],
        "refund_return_adjusted_margin": {
            "gross_paid_sales": round(sum(final_sales), 2),
            "refunds": round(sum(refunds), 2),
            "mature_contribution_margin": round(sum(mature_margins), 2),
            "return_count": sum(1 for thread in matured_threads if bool(thread["outcome"]["outcome"].get("returned"))),
            "paid_outcome_count": len(paid_threads),
        },
        "merchant_value_statement": _merchant_value_statement(accepted_final_sales, accepted_mature_margins, accepted_refunds),
    }


def marginpilot_rule_simulation(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    *,
    rule: dict[str, Any] | None = None,
    merchant_id: str | None = None,
) -> dict[str, Any]:
    rule_body = _normalize_rule(rule or {})
    threads = [thread for thread in _offer_threads(_events(data_dir, merchant_id=merchant_id)) if thread["offer"] is not None]
    rows = []
    for thread in threads:
        simulated = _simulate_rule_for_thread(thread, rule_body)
        actual = _normalize_action(thread["decision"]["selected_action"]) if thread["decision"] is not None else None
        outcome = thread["outcome"]["outcome"] if thread["outcome"] is not None else {}
        actions_match = _actions_match(actual, simulated)
        actual_margin = outcome.get("mature_contribution_margin") if actions_match and outcome.get("return_window_matured") else None
        rows.append(
            {
                "offer_id": thread["offer"]["offer_id"],
                "listing_id": thread["offer"]["listing_id"],
                "actual_selected_action": actual,
                "simulated_action": simulated,
                "actions_match": actions_match,
                "observed_mature_margin": actual_margin,
                "observed_outcome_reused": actual_margin is not None,
                "observed_outcome_reuse_reason": "actual_action_matched_simulated_rule" if actual_margin is not None else "not_reused_for_counterfactual_action",
            }
        )
    comparable = [row for row in rows if row["observed_mature_margin"] is not None]
    matched = [row for row in comparable if row["actions_match"]]
    return {
        "schema_version": "marginpilot_rule_simulation.v1",
        "product_id": MARGINPILOT_PRODUCT_ID,
        "merchant_id": merchant_id,
        "rule": rule_body,
        "not_causal": True,
        "causal_claim": False,
        "model_training": "not_run",
        "automation_allowed": False,
        "rows": rows,
        "summary": {
            "eligible_offers": len(rows),
            "observed_mature_rows": len(comparable),
            "action_counts": _action_counts(row["simulated_action"] for row in rows),
            "matched_actual_actions": len(matched),
            "mismatched_actions": sum(1 for row in rows if row["actual_selected_action"] is not None and not row["actions_match"]),
            "observed_margin_when_actions_matched": round(sum(float(row["observed_mature_margin"]) for row in matched), 2),
        },
        "interpretation": "This replays a fixed rule against historical contexts. It is not a causal estimate of what would have happened.",
    }


def _events(data_dir: str | Path, *, merchant_id: str | None) -> list[dict[str, Any]]:
    ledger = ImmutableLedger(Path(data_dir) / "ledger.jsonl")
    events = ledger.payloads(MARGINPILOT_RECORD_TYPE)
    if merchant_id is not None:
        events = [event for event in events if event.get("merchant_id") == merchant_id]
    return events


def _latest_consent(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    consents = [event for event in events if event["event_type"] == "merchant_consent"]
    if not consents:
        return None
    return sorted(consents, key=lambda event: parse_time(str(event["occurred_at"])))[-1]


def _latest_consent_by_merchant(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_merchant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event["event_type"] == "merchant_consent":
            by_merchant[str(event["merchant_id"])].append(event)
    return {
        merchant_id: sorted(consents, key=lambda event: parse_time(str(event["occurred_at"])))[-1]
        for merchant_id, consents in by_merchant.items()
    }


def _consent_authorizes_learning(consent_event: dict[str, Any] | None) -> bool:
    consent = consent_event.get("consent", {}) if consent_event else {}
    return bool(consent.get("merchant_specific_learning_authorized")) and bool(consent.get("pii_exclusion_acknowledged"))


def _merchant_offer_key(event: dict[str, Any]) -> tuple[str, str]:
    return (str(event["merchant_id"]), str(event["offer_id"]))


def _payloads_have_no_pii(events: list[dict[str, Any]]) -> bool:
    try:
        for event in events:
            _reject_pii(event)
    except MarginPilotError:
        return False
    return True


def _thread_integrity(events: list[dict[str, Any]]) -> dict[str, Any]:
    opened: dict[tuple[str, str], dict[str, Any]] = {}
    decisions: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    outcomes: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    errors: list[str] = []
    for event in events:
        if event["event_type"] == "offer_opened":
            key = _merchant_offer_key(event)
            if key in opened:
                errors.append(f"duplicate offer_opened for merchant={key[0]} offer={key[1]}")
            opened[key] = event
        elif event["event_type"] == "merchant_decision":
            decisions[_merchant_offer_key(event)].append(event)
        elif event["event_type"] == "outcome_matured":
            outcomes[_merchant_offer_key(event)].append(event)

    for key, related_decisions in decisions.items():
        offer = opened.get(key)
        if offer is None:
            errors.append(f"merchant_decision without offer_opened for merchant={key[0]} offer={key[1]}")
            continue
        offer_time = parse_time(str(offer["occurred_at"]))
        available = {_action_signature(_normalize_action(action)) for action in offer["available_actions"]}
        for decision in related_decisions:
            if parse_time(str(decision["occurred_at"])) < offer_time:
                errors.append(f"merchant_decision before offer_opened for merchant={key[0]} offer={key[1]}")
            selected = _action_signature(_normalize_action(decision["selected_action"]))
            if selected not in available:
                errors.append(f"merchant_decision selected unavailable action for merchant={key[0]} offer={key[1]}")

    for key, related_outcomes in outcomes.items():
        offer = opened.get(key)
        if offer is None:
            errors.append(f"outcome_matured without offer_opened for merchant={key[0]} offer={key[1]}")
            continue
        offer_time = parse_time(str(offer["occurred_at"]))
        for outcome in related_outcomes:
            if parse_time(str(outcome["occurred_at"])) < offer_time:
                errors.append(f"outcome_matured before offer_opened for merchant={key[0]} offer={key[1]}")

    return {
        "passed": not errors,
        "errors": errors,
        "offers_checked": len(opened),
        "decisions_checked": sum(len(items) for items in decisions.values()),
        "outcomes_checked": sum(len(items) for items in outcomes.values()),
    }


def _offer_threads(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    offers: dict[tuple[str, str], dict[str, Any]] = {}
    decisions: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    outcomes: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event["event_type"] == "offer_opened":
            offers[_merchant_offer_key(event)] = event
        elif event["event_type"] == "merchant_decision":
            decisions[_merchant_offer_key(event)].append(event)
        elif event["event_type"] == "outcome_matured":
            outcomes[_merchant_offer_key(event)].append(event)
    threads = []
    for key, offer in sorted(offers.items(), key=lambda item: (str(item[1]["occurred_at"]), item[0])):
        decision = _latest_event(decisions.get(key, []))
        outcome = _latest_event(outcomes.get(key, []))
        threads.append({"key": key, "offer": offer, "decision": decision, "outcome": outcome})
    return threads


def _latest_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not events:
        return None
    return sorted(events, key=lambda event: parse_time(str(event["occurred_at"])))[-1]


def _is_accepting_decision(decision: dict[str, Any] | None) -> bool:
    action = _decision_action(decision)
    return action in {"accept", "counter_at_amount", "free_shipping_counter", "bundle_counter"}


def _decision_action(decision: dict[str, Any] | None) -> str | None:
    if decision is None:
        return None
    return str(decision.get("selected_action", {}).get("action"))


def _thread_has_paid_outcome(thread: dict[str, Any]) -> bool:
    outcome = thread["outcome"]["outcome"] if thread.get("outcome") is not None else {}
    return bool(outcome.get("buyer_paid")) and bool(outcome.get("return_window_matured")) and not bool(outcome.get("cancelled"))


def _mature_margin_row(thread: dict[str, Any]) -> dict[str, Any]:
    offer = thread["offer"]
    decision = thread["decision"]
    outcome = thread["outcome"]["outcome"]
    return {
        "offer_id": offer["offer_id"],
        "listing_id": offer["listing_id"],
        "category": offer["pre_decision_context"].get("category"),
        "selected_action": _decision_action(decision),
        "final_sale_price": float(outcome["final_sale_price"]),
        "refund_amount": float(outcome.get("refund_amount") or 0.0),
        "returned": bool(outcome.get("returned")),
        "mature_contribution_margin": float(outcome["mature_contribution_margin"]),
    }


def _margin_by_product_and_age(threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for thread in threads:
        context = thread["offer"]["pre_decision_context"]
        key = str(context.get("comparable_inventory_key") or context.get("sku") or thread["offer"]["listing_id"])
        age_bucket = _age_bucket(context.get("inventory_age_days"))
        groups[(key, age_bucket)].append(float(thread["outcome"]["outcome"]["mature_contribution_margin"]))
    rows = []
    for (product_key, age_bucket), margins in sorted(groups.items()):
        rows.append(
            {
                "product_key": product_key,
                "inventory_age_bucket": age_bucket,
                "paid_outcomes": len(margins),
                "total_mature_margin": round(sum(margins), 2),
                "average_mature_margin": round(sum(margins) / len(margins), 2),
            }
        )
    return rows


def _age_bucket(value: Any) -> str:
    if value is None:
        return "unknown"
    days = float(value)
    if days <= 7:
        return "0-7"
    if days <= 30:
        return "8-30"
    if days <= 60:
        return "31-60"
    return "61+"


def _concession_row(thread: dict[str, Any]) -> dict[str, Any] | None:
    offer = thread["offer"]
    decision = thread["decision"]
    if decision is None:
        return None
    context = offer["pre_decision_context"]
    selected = _normalize_action(decision["selected_action"])
    amount = _action_amount(selected, context)
    if amount is None:
        return None
    asking = float(context["asking_price"])
    conceded = max(asking - amount, 0.0)
    return {
        "offer_id": offer["offer_id"],
        "listing_id": offer["listing_id"],
        "selected_action": selected["action"],
        "asking_price": asking,
        "selected_amount": round(amount, 2),
        "amount_conceded": round(conceded, 2),
        "concession_rate": round(conceded / asking, 4) if asking > 0 else 0.0,
    }


def _time_to_payment_summary(threads: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for thread in threads:
        outcome = thread["outcome"]["outcome"]
        seconds: float | None = None
        source = "unavailable"
        if isinstance(outcome.get("paid_at"), str):
            seconds = (parse_time(str(outcome["paid_at"])) - parse_time(str(thread["offer"]["occurred_at"]))).total_seconds()
            source = "paid_at"
        elif outcome.get("inventory_days_until_sale") is not None:
            seconds = float(outcome["inventory_days_until_sale"]) * 86400
            source = "inventory_days_until_sale"
        if seconds is not None:
            rows.append(
                {
                    "offer_id": thread["offer"]["offer_id"],
                    "seconds": round(seconds, 2),
                    "days": round(seconds / 86400, 3),
                    "source": source,
                }
            )
    return {
        "rows": rows,
        "average_seconds": _mean([row["seconds"] for row in rows]),
        "average_days": _mean([row["days"] for row in rows]),
    }


def _unpaid_row(thread: dict[str, Any]) -> dict[str, Any]:
    offer = thread["offer"]
    decision = thread["decision"]
    outcome = thread["outcome"]["outcome"] if thread["outcome"] is not None else {}
    return {
        "offer_id": offer["offer_id"],
        "listing_id": offer["listing_id"],
        "selected_action": _decision_action(decision),
        "selected_amount": _normalize_action(decision["selected_action"])["amount"] if decision is not None else None,
        "has_mature_outcome": thread["outcome"] is not None,
        "buyer_paid": bool(outcome.get("buyer_paid")),
        "cancelled": bool(outcome.get("cancelled")),
    }


def _merchant_value_statement(final_sales: list[float], mature_margins: list[float], refunds: list[float]) -> str:
    if not final_sales:
        return "No accepted offers have mature paid outcomes yet."
    return (
        f"I accepted ${sum(final_sales):,.2f} in paid offers, "
        f"but only ${sum(mature_margins):,.2f} matured into contribution margin "
        f"after ${sum(refunds):,.2f} of refunds."
    )


def _normalize_rule(rule: dict[str, Any]) -> dict[str, Any]:
    rule_type = str(rule.get("rule_type") or "counter_percent_above_offer")
    if rule_type not in {"counter_percent_above_offer", "accept_if_margin_then_counter", "decline_below_margin_floor"}:
        raise MarginPilotError(f"unknown fixed rule type: {rule_type!r}")
    normalized = {
        "rule_type": rule_type,
        "counter_markup_pct": float(rule.get("counter_markup_pct", 0.08)),
        "max_counter_to_asking_ratio": float(rule.get("max_counter_to_asking_ratio", 1.0)),
        "min_margin_buffer": float(rule.get("min_margin_buffer", 0.0)),
    }
    if normalized["counter_markup_pct"] < 0:
        raise MarginPilotError("counter_markup_pct may not be negative")
    if normalized["max_counter_to_asking_ratio"] <= 0:
        raise MarginPilotError("max_counter_to_asking_ratio must be positive")
    if normalized["min_margin_buffer"] < 0:
        raise MarginPilotError("min_margin_buffer may not be negative")
    return normalized


def _simulate_rule_for_thread(thread: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
    offer = thread["offer"]
    context = offer["pre_decision_context"]
    available = [_normalize_action(action) for action in offer["available_actions"]]
    buyer_offer = float(context["buyer_offer_amount"])
    floor = float(context["merchant_floor_mature_margin"]) + float(rule["min_margin_buffer"])
    accept_margin = _mature_margin_if_sold(buyer_offer, {"action": "accept", "amount": buyer_offer}, context) if context.get("cost_basis") is not None else None
    if rule["rule_type"] in {"accept_if_margin_then_counter", "decline_below_margin_floor"} and accept_margin is not None and accept_margin >= floor:
        return _available_or_abstain({"action": "accept", "amount": buyer_offer}, available)
    if rule["rule_type"] == "decline_below_margin_floor" and accept_margin is not None and accept_margin < floor:
        return _available_or_abstain({"action": "decline", "amount": None}, available)
    counter = min(float(context["asking_price"]) * float(rule["max_counter_to_asking_ratio"]), buyer_offer * (1.0 + float(rule["counter_markup_pct"])))
    counter = round(counter, 2)
    if context.get("cost_basis") is not None:
        counter_margin = _mature_margin_if_sold(counter, {"action": "counter_at_amount", "amount": counter}, context)
        if counter_margin < floor:
            return _available_or_abstain({"action": "decline", "amount": None}, available)
    return _available_or_abstain({"action": "counter_at_amount", "amount": counter}, available)


def _available_or_abstain(candidate: dict[str, Any], available: list[dict[str, Any]]) -> dict[str, Any]:
    action = candidate["action"]
    amount = candidate.get("amount")
    if action in {"decline", "wait"} and any(item["action"] == action for item in available):
        return {"action": action, "amount": None, "available": True}
    same_action = [item for item in available if item["action"] == action]
    if not same_action:
        return {"action": "abstain", "amount": None, "available": False}
    if amount is None:
        return {"action": action, "amount": None, "available": True}
    closest = min(same_action, key=lambda item: abs(float(item["amount"] or 0.0) - float(amount)))
    return {"action": action, "amount": closest["amount"], "available": True}


def _action_counts(actions: Any) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for action in actions:
        if isinstance(action, dict):
            key = str(action.get("action"))
        else:
            key = str(action)
        counts[key] += 1
    return dict(sorted(counts.items()))


def _actions_match(actual: dict[str, Any] | None, simulated: dict[str, Any]) -> bool:
    if actual is None:
        return False
    if actual["action"] != simulated["action"]:
        return False
    if actual.get("amount") is None or simulated.get("amount") is None:
        return actual.get("amount") is None and simulated.get("amount") is None
    return abs(float(actual["amount"]) - float(simulated["amount"])) <= 0.01


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(float(value) for value in values) / len(values), 4)


def _action_signature(action: dict[str, Any]) -> tuple[str, float | None]:
    return (str(action["action"]), action["amount"])


def _validate_consent(event: dict[str, Any]) -> None:
    consent = event.get("consent")
    if not isinstance(consent, dict):
        raise MarginPilotError("merchant_consent requires consent object")
    if consent.get("merchant_specific_learning_authorized") is not True:
        raise MarginPilotError("merchant_specific_learning_authorized must be true for MarginPilot learning consent")
    if consent.get("cross_merchant_pooling_authorized") not in {True, False}:
        raise MarginPilotError("cross_merchant_pooling_authorized must be explicit true/false")
    if consent.get("pii_exclusion_acknowledged") is not True:
        raise MarginPilotError("pii_exclusion_acknowledged must be true")
    if not isinstance(consent.get("authorized_uses"), list) or not consent["authorized_uses"]:
        raise MarginPilotError("authorized_uses must be a non-empty list")
    if not isinstance(consent.get("written_consent_reference"), str) or not consent["written_consent_reference"].strip():
        raise MarginPilotError("written_consent_reference is required")
    if not isinstance(consent.get("consent_text_hash"), str) or not consent["consent_text_hash"].strip():
        raise MarginPilotError("consent_text_hash is required")


def _validate_offer_opened(event: dict[str, Any]) -> None:
    for key in ["offer_id", "listing_id", "surface", "observation_cutoff"]:
        if not isinstance(event.get(key), str) or not str(event[key]).strip():
            raise MarginPilotError(f"offer_opened requires non-empty {key}")
    if event["surface"] not in SURFACES:
        raise MarginPilotError(f"surface must be one of {sorted(SURFACES)}")
    if parse_time(str(event["observation_cutoff"])) > parse_time(str(event["occurred_at"])):
        raise MarginPilotError("observation_cutoff may not be after occurred_at")
    context = event.get("pre_decision_context")
    if not isinstance(context, dict):
        raise MarginPilotError("pre_decision_context must be an object")
    _reject_post_decision_context(context)
    required = ["listing_id", "category", "currency", "asking_price", "buyer_offer_amount", "platform_fee_rate", "shipping_cost", "merchant_floor_mature_margin"]
    missing = [key for key in required if key not in context]
    if missing:
        raise MarginPilotError(f"pre_decision_context missing fields: {missing}")
    if context["listing_id"] != event["listing_id"]:
        raise MarginPilotError("pre_decision_context.listing_id must match listing_id")
    _validate_money_mapping(context)
    if context.get("cost_basis") is not None and float(context["cost_basis"]) < 0:
        raise MarginPilotError("cost_basis may not be negative")
    actions = event.get("available_actions")
    if not isinstance(actions, list) or not actions:
        raise MarginPilotError("available_actions must be a non-empty list")
    for action in actions:
        normalized = _normalize_action(action)
        if normalized["action"] == "manual_other":
            raise MarginPilotError("available_actions must enumerate concrete surface actions, not manual_other")


def _validate_merchant_decision(event: dict[str, Any]) -> None:
    if not isinstance(event.get("offer_id"), str) or not event["offer_id"].strip():
        raise MarginPilotError("merchant_decision requires offer_id")
    action = _normalize_action(event.get("selected_action"))
    if action["action"] not in MERCHANT_DECISIONS:
        raise MarginPilotError("selected_action is not a merchant decision")
    assignment = event.get("assignment")
    if not isinstance(assignment, dict):
        raise MarginPilotError("merchant_decision requires assignment object")
    probability = assignment.get("assignment_probability")
    if probability is None or not 0 < float(probability) <= 1:
        raise MarginPilotError("assignment_probability must be in (0, 1]")
    if bool(assignment.get("randomized")) and float(probability) in {0.0, 1.0}:
        raise MarginPilotError("randomized decisions require non-degenerate assignment_probability")


def _validate_outcome(event: dict[str, Any]) -> None:
    for key in ["offer_id", "order_id"]:
        if not isinstance(event.get(key), str) or not event[key].strip():
            raise MarginPilotError(f"outcome_matured requires {key}")
    outcome = event.get("outcome")
    if not isinstance(outcome, dict):
        raise MarginPilotError("outcome must be an object")
    for key in ["buyer_paid", "returned", "cancelled", "return_window_matured"]:
        if not isinstance(outcome.get(key), bool):
            raise MarginPilotError(f"outcome.{key} must be boolean")
    _validate_money_mapping(outcome)
    if outcome["return_window_matured"] is not True:
        raise MarginPilotError("outcome_matured requires return_window_matured true")
    if outcome["buyer_paid"] and outcome.get("mature_contribution_margin") is None:
        raise MarginPilotError("paid mature outcomes require mature_contribution_margin")
    if outcome["buyer_paid"]:
        _validate_mature_margin_components(outcome)


def _normalize_action(action: Any) -> dict[str, Any]:
    if not isinstance(action, dict):
        raise MarginPilotError("action must be an object")
    action_name = action.get("action")
    if action_name not in ACTION_TYPES and action_name != "manual_other":
        raise MarginPilotError(f"unknown action: {action_name!r}")
    amount = action.get("amount")
    if action_name in {"accept", "counter_at_amount", "free_shipping_counter", "bundle_counter"}:
        if amount is None or float(amount) <= 0:
            raise MarginPilotError(f"{action_name} requires positive amount")
    elif amount is not None:
        raise MarginPilotError(f"{action_name} may not include amount")
    return {"action": action_name, "amount": round(float(amount), 2) if amount is not None else None}


def _action_economics(event: dict[str, Any]) -> list[dict[str, Any]]:
    context = event["pre_decision_context"]
    rows = []
    for raw_action in event["available_actions"]:
        action = _normalize_action(raw_action)
        amount = _action_amount(action, context)
        margin = None
        floor_violation = False
        if amount is not None and context.get("cost_basis") is not None:
            margin = _mature_margin_if_sold(amount, action, context)
            floor_violation = margin < float(context["merchant_floor_mature_margin"])
        rows.append(
            {
                "action": action["action"],
                "amount": amount,
                "mature_margin_if_sold": round(margin, 2) if margin is not None else None,
                "violates_merchant_floor": floor_violation,
                "accounting_complete": margin is not None,
            }
        )
    return rows


def _action_amount(action: dict[str, Any], context: dict[str, Any]) -> float | None:
    if action["action"] == "accept":
        return float(action.get("amount") or context["buyer_offer_amount"])
    if action["action"] in {"counter_at_amount", "free_shipping_counter", "bundle_counter"}:
        return float(action["amount"])
    return None


def _mature_margin_if_sold(amount: float, action: dict[str, Any], context: dict[str, Any]) -> float:
    platform_fee = amount * float(context.get("platform_fee_rate") or 0.0)
    payment_fee_flat = float(context.get("payment_fee_flat") or 0.0)
    shipping = float(context.get("shipping_cost") or 0.0)
    fulfillment = float(context.get("fulfillment_cost") or 0.0)
    return_allowance = float(context.get("return_allowance") or 0.0)
    cost_basis = float(context["cost_basis"])
    return amount - platform_fee - payment_fee_flat - shipping - fulfillment - return_allowance - cost_basis


def _mature_paid(outcome: dict[str, Any]) -> bool:
    return bool(outcome.get("buyer_paid")) and bool(outcome.get("return_window_matured")) and not bool(outcome.get("cancelled")) and outcome.get("mature_contribution_margin") is not None


def _reject_pii(value: Any, *, path: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if _is_pii_key(key_text, path):
                raise MarginPilotError(f"customer PII field is not allowed: {path + key_text}")
            _reject_pii(item, path=f"{path}{key_text}.")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_pii(item, path=f"{path}{index}.")
    elif isinstance(value, str) and _is_pii_value(value):
        raise MarginPilotError(f"customer PII value is not allowed at: {path.rstrip('.')}")


def _is_pii_key(key: str, path: str) -> bool:
    lower = key.lower()
    if lower in PII_KEYS:
        return True
    tokens = _key_tokens(lower)
    path_tokens = _key_tokens(path.lower().replace(".", "_"))
    context_tokens = tokens | path_tokens
    if tokens & PII_CONTACT_TOKENS:
        return True
    if context_tokens & PII_CONTEXT_KEYS and tokens & PII_IDENTIFIER_TOKENS:
        return True
    if context_tokens & PII_CONTEXT_KEYS and "name" in tokens:
        return True
    if context_tokens & PII_CONTEXT_KEYS and tokens & PII_TEXT_TOKENS:
        return True
    if ("buyer" in tokens or "customer" in tokens or "contact" in tokens) and tokens & (
        PII_IDENTIFIER_TOKENS | {"email", "phone", "address", "name"}
    ):
        return True
    return False


def _key_tokens(text: str) -> set[str]:
    for separator in [".", "-", ":", "/", "\\"]:
        text = text.replace(separator, "_")
    return {token for token in text.split("_") if token}


def _is_pii_value(text: str) -> bool:
    return any(pattern.search(text) for pattern in PII_VALUE_PATTERNS)


def _reject_post_decision_context(context: dict[str, Any]) -> None:
    forbidden = sorted(set(context) & POST_DECISION_CONTEXT_KEYS)
    if forbidden:
        raise MarginPilotError(f"pre_decision_context contains post-decision fields: {forbidden}")


def _validate_money_mapping(value: dict[str, Any]) -> None:
    for key, item in value.items():
        if key not in MONEY_FIELDS or item is None:
            continue
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise MarginPilotError(f"{key} must be numeric")
        if not math.isfinite(float(item)):
            raise MarginPilotError(f"{key} must be finite")
        if key != "mature_contribution_margin" and float(item) < 0:
            raise MarginPilotError(f"{key} may not be negative")
    rate = value.get("platform_fee_rate")
    if rate is not None and not 0 <= float(rate) < 1:
        raise MarginPilotError("platform_fee_rate must be in [0, 1)")


def _validate_mature_margin_components(outcome: dict[str, Any]) -> None:
    required = [
        "final_sale_price",
        "actual_fees",
        "actual_shipping_cost",
        "actual_fulfillment_cost",
        "cost_basis",
        "refund_amount",
        "mature_contribution_margin",
    ]
    missing = [key for key in required if outcome.get(key) is None]
    if missing:
        raise MarginPilotError(f"paid mature outcomes require margin component evidence: {missing}")
    computed = (
        float(outcome["final_sale_price"])
        - float(outcome["actual_fees"])
        - float(outcome["actual_shipping_cost"])
        - float(outcome["actual_fulfillment_cost"])
        - float(outcome["cost_basis"])
        - float(outcome["refund_amount"])
    )
    reported = float(outcome["mature_contribution_margin"])
    if abs(round(computed, 2) - round(reported, 2)) > 0.01:
        raise MarginPilotError(
            f"mature_contribution_margin {reported:.2f} does not match component evidence {computed:.2f}"
        )


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)
