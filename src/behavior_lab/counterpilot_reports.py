from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any

from behavior_lab.counterpilot import DEFAULT_DATA_DIR
from behavior_lab.counterpilot_state import TransactionStateMachine
from behavior_lab.counterpilot_state.machine import COUNTERPILOT_STATE_RECORD_TYPE
from behavior_lab.counterpilot_storage import assert_no_pii
from behavior_lab.ledger import ImmutableLedger


DEFAULT_REPORT_DATA_DIR = DEFAULT_DATA_DIR / "transaction_core"
REPORT_SCHEMA_VERSION = "counterpilot_merchant_report.v1"


def build_counterpilot_report(
    data_dir: str | Path = DEFAULT_REPORT_DATA_DIR,
    *,
    merchant_namespace: str | None = None,
    rule: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data_root = Path(data_dir)
    events = _transaction_events(data_root, merchant_namespace=merchant_namespace)
    groups = _group_transactions(events)
    transactions = [
        _transaction_summary(data_root, namespace, transaction_id, items)
        for (namespace, transaction_id), items in sorted(groups.items())
    ]
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "merchant_namespace": merchant_namespace,
        "transaction_count": len(transactions),
        "offer_funnel": _offer_funnel(transactions),
        "mature_margin_summary": _mature_margin_summary(transactions),
        "margin_leakage": _margin_leakage(transactions),
        "breakdowns": {
            "by_product_sku": _breakdown(transactions, "product_sku"),
            "by_inventory_age_bucket": _breakdown(transactions, "inventory_age_bucket"),
            "by_offer_to_asking_ratio_bucket": _breakdown(transactions, "offer_to_asking_ratio_bucket"),
        },
        "rule_simulator": _simulate_rule(transactions, rule or {}),
        "transactions": transactions,
        "claims": {
            "causal_lift_claimed": False,
            "prediction_models_used": False,
            "automation_used": False,
        },
    }
    assert_no_pii(report)
    return report


def counterpilot_report_markdown(report: dict[str, Any]) -> str:
    summary = report["mature_margin_summary"]
    leakage = report["margin_leakage"]
    funnel = report["offer_funnel"]
    lines = [
        "# Counterpilot Merchant Report",
        "",
        f"- Transactions: `{report['transaction_count']}`",
        f"- Merchant namespace: `{report.get('merchant_namespace') or 'all'}`",
        f"- Mature-margin totals provisional: `{summary['totals_provisional']}`",
        "",
        "## Offer Funnel",
        "",
    ]
    for key in [
        "offers_submitted",
        "merchant_accepted",
        "merchant_countered",
        "buyer_accepted",
        "checkout_created",
        "paid",
        "cancelled",
        "refunded",
        "mature",
    ]:
        lines.append(f"- {key}: `{funnel[key]}`")
    lines.extend(
        [
            "",
            "## Mature Margin",
            "",
            f"- Gross negotiated revenue: `{summary['gross_negotiated_revenue_minor']}` minor units",
            f"- Item cost: `{summary['item_cost_minor']}` minor units",
            f"- Shipping charged: `{summary['shipping_charged_minor']}` minor units",
            f"- Shipping cost: `{summary['shipping_cost_minor']}` minor units",
            f"- Free-shipping cost: `{summary['free_shipping_cost_minor']}` minor units",
            f"- Platform/payment fees: `{summary['platform_payment_fees_minor']}` minor units",
            f"- Refund amount: `{summary['refund_amount_minor']}` minor units",
            f"- Return loss: `{summary['return_loss_minor']}` minor units",
            f"- Mature contribution margin: `{summary['mature_contribution_margin_minor']}` minor units",
            "",
            "## Margin Leakage",
            "",
            f"- Free shipping: `{leakage['free_shipping_minor']}` minor units",
            f"- Refunds/returns: `{leakage['refunds_returns_minor']}` minor units",
            f"- Unpaid accepted offers: `{leakage['unpaid_accepted_offers']}`",
            f"- Expired checkout links: `{leakage['expired_checkout_links']}`",
            f"- Missing cost basis: `{leakage['missing_cost_basis']}`",
            f"- Missing fees: `{leakage['missing_fees']}`",
            f"- Immature outcomes: `{leakage['immature_outcomes']}`",
            "",
            "## Rule Simulator",
            "",
            report["rule_simulator"]["label"],
        ]
    )
    return "\n".join(lines) + "\n"


def write_counterpilot_report(
    data_dir: str | Path = DEFAULT_REPORT_DATA_DIR,
    *,
    output: str | Path | None = None,
    output_format: str = "json",
    merchant_namespace: str | None = None,
    rule: dict[str, Any] | None = None,
) -> dict[str, Any] | str:
    report = build_counterpilot_report(data_dir, merchant_namespace=merchant_namespace, rule=rule)
    if output_format == "json":
        rendered = json.dumps(report, indent=2, sort_keys=True)
        result: dict[str, Any] | str = report
    elif output_format == "markdown":
        rendered = counterpilot_report_markdown(report)
        result = rendered
    else:
        raise ValueError("output_format must be 'json' or 'markdown'")
    if output is not None:
        Path(output).write_text(rendered + ("" if rendered.endswith("\n") else "\n"), encoding="utf-8")
    return result


def _transaction_events(data_root: Path, *, merchant_namespace: str | None) -> list[dict[str, Any]]:
    ledger = ImmutableLedger(data_root / "counterpilot_transactions.jsonl")
    events = ledger.payloads(COUNTERPILOT_STATE_RECORD_TYPE)
    if merchant_namespace is not None:
        events = [event for event in events if event.get("merchant_namespace") == merchant_namespace]
    return events


def _group_transactions(events: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        groups[(str(event["merchant_namespace"]), str(event["transaction_id"]))].append(event)
    return groups


def _transaction_summary(data_root: Path, namespace: str, transaction_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    snapshot = TransactionStateMachine(data_root).inspect(namespace, transaction_id)
    transitions = [event["transition_to"] for event in events]
    offer = _first_transition(events, "offer_submitted") or {}
    mature = _first_transition(events, "mature") or {}
    components = _financial_components(events, mature)
    missing = _missing_components(components, snapshot)
    sku = _sku(offer)
    asking = _money_minor(_first_money(offer, ["line_items", 0, "unit_price"]))
    buyer_offer = _money_minor(_first_money(offer, ["economics", "buyer_offer"]))
    return {
        "merchant_namespace": namespace,
        "transaction_id": transaction_id,
        "current_state": snapshot["current_state"],
        "transitions": sorted(set(transitions)),
        "applied_event_ids": snapshot["applied_event_ids"],
        "pending_event_ids": snapshot["pending_event_ids"],
        "error_count": len(snapshot["errors"]),
        "product_sku": sku,
        "inventory_age_bucket": _inventory_age_bucket(_event_number(events, "inventory_age_days")),
        "offer_to_asking_ratio_bucket": _ratio_bucket(buyer_offer, asking),
        "financial_components": components,
        "complete_for_mature_margin": not missing,
        "missing_components": missing,
    }


def _offer_funnel(transactions: list[dict[str, Any]]) -> dict[str, int]:
    def has(transaction: dict[str, Any], *states: str) -> bool:
        return bool(set(transaction["transitions"]) & set(states))

    return {
        "offers_submitted": sum(1 for item in transactions if has(item, "offer_submitted")),
        "merchant_accepted": sum(1 for item in transactions if has(item, "merchant_accepted")),
        "merchant_countered": sum(1 for item in transactions if has(item, "merchant_countered")),
        "buyer_accepted": sum(1 for item in transactions if has(item, "buyer_accepted")),
        "checkout_created": sum(1 for item in transactions if has(item, "checkout_created")),
        "paid": sum(1 for item in transactions if has(item, "paid")),
        "cancelled": sum(1 for item in transactions if has(item, "cancelled")),
        "refunded": sum(1 for item in transactions if has(item, "partially_refunded", "fully_refunded")),
        "mature": sum(1 for item in transactions if has(item, "mature")),
    }


def _mature_margin_summary(transactions: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [item for item in transactions if "mature" in item["transitions"] and item["complete_for_mature_margin"]]
    provisional = [item for item in transactions if "mature" in item["transitions"] and not item["complete_for_mature_margin"]]
    fields = [
        "gross_negotiated_revenue_minor",
        "item_cost_minor",
        "shipping_charged_minor",
        "shipping_cost_minor",
        "free_shipping_cost_minor",
        "platform_payment_fees_minor",
        "refund_amount_minor",
        "return_loss_minor",
        "mature_contribution_margin_minor",
    ]
    totals = {field: sum(int(item["financial_components"].get(field) or 0) for item in complete) for field in fields}
    totals.update(
        {
            "currency": _single_currency(complete),
            "complete_mature_transactions": len(complete),
            "excluded_mature_transactions": [
                {"transaction_id": item["transaction_id"], "missing_components": item["missing_components"]}
                for item in provisional
            ],
            "totals_provisional": bool(provisional),
        }
    )
    return totals


def _margin_leakage(transactions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "free_shipping_minor": sum(int(item["financial_components"].get("free_shipping_cost_minor") or 0) for item in transactions),
        "refunds_returns_minor": sum(
            int(item["financial_components"].get("refund_amount_minor") or 0)
            + int(item["financial_components"].get("return_loss_minor") or 0)
            for item in transactions
        ),
        "unpaid_accepted_offers": sum(
            1
            for item in transactions
            if set(item["transitions"]) & {"merchant_accepted", "buyer_accepted"} and "paid" not in item["transitions"]
        ),
        "expired_checkout_links": sum(1 for item in transactions if "checkout_expired" in item["transitions"]),
        "missing_cost_basis": sum(1 for item in transactions if "item_cost_minor" in item["missing_components"]),
        "missing_fees": sum(1 for item in transactions if "platform_payment_fees_minor" in item["missing_components"]),
        "immature_outcomes": sum(1 for item in transactions if "mature" not in item["transitions"]),
    }


def _breakdown(transactions: list[dict[str, Any]], key: str) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for item in transactions:
        bucket = str(item.get(key) or "unknown")
        counts[bucket]["transactions"] += 1
        if "mature" in item["transitions"]:
            counts[bucket]["mature"] += 1
        if item["complete_for_mature_margin"]:
            counts[bucket]["complete_margin"] += int(item["financial_components"].get("mature_contribution_margin_minor") or 0)
    return {bucket: dict(counter) for bucket, counter in sorted(counts.items())}


def _simulate_rule(transactions: list[dict[str, Any]], rule: dict[str, Any]) -> dict[str, Any]:
    accept_pct = float(rule.get("accept_above_asking_pct", 0.85))
    counter_markup_pct = float(rule.get("counter_markup_pct", 0.08))
    floor_buffer_minor = int(rule.get("decline_below_floor_buffer_minor", 0))
    decisions = []
    counts: Counter[str] = Counter()
    for item in transactions:
        components = item["financial_components"]
        asking = components.get("asking_price_minor")
        buyer_offer = components.get("buyer_offer_amount_minor")
        floor = components.get("merchant_floor_minor")
        if not isinstance(asking, int) or not isinstance(buyer_offer, int):
            action = "abstain_missing_offer_context"
            amount = None
        elif floor is not None and buyer_offer < int(floor) + floor_buffer_minor:
            action = "decline"
            amount = None
        elif asking > 0 and buyer_offer / asking >= accept_pct:
            action = "accept"
            amount = buyer_offer
        else:
            action = "counter_at_amount"
            amount = int(round(buyer_offer * (1.0 + counter_markup_pct)))
        counts[action] += 1
        decisions.append({"transaction_id": item["transaction_id"], "selected_action": action, "amount_minor": amount})
    return {
        "label": "Retrospective deterministic rule simulation only; non-causal and not a profit-lift estimate.",
        "rule": {
            "accept_above_asking_pct": accept_pct,
            "counter_markup_pct": counter_markup_pct,
            "decline_below_floor_buffer_minor": floor_buffer_minor,
        },
        "decision_counts": dict(counts),
        "decisions": decisions,
    }


def _financial_components(events: list[dict[str, Any]], mature: dict[str, Any]) -> dict[str, Any]:
    offer = _first_transition(events, "offer_submitted") or {}
    paid = _last_transition(events, "paid") or {}
    components = {
        "currency": _currency(events),
        "asking_price_minor": _money_minor(_first_money(offer, ["line_items", 0, "unit_price"])),
        "buyer_offer_amount_minor": _money_minor(_first_money(offer, ["economics", "buyer_offer"])),
        "merchant_floor_minor": _money_minor(_first_money(events, ["economics", "merchant_floor"])),
        "gross_negotiated_revenue_minor": _money_minor(_first_money(paid, ["economics", "final_sale_price"])),
        "item_cost_minor": _money_minor(_first_money(events, ["economics", "cost_basis"])),
        "shipping_charged_minor": _money_minor(_first_money(paid, ["economics", "shipping_charged"])) or 0,
        "shipping_cost_minor": _money_minor(_first_money(events, ["economics", "shipping_cost"])) or 0,
        "free_shipping_cost_minor": _shipping_discount_minor(events),
        "platform_payment_fees_minor": _money_minor(_first_money(mature, ["mature_outcome", "reconciled_fees"])),
        "refund_amount_minor": _refund_amount_minor(events),
        "return_loss_minor": _money_minor(_first_money(mature, ["mature_outcome", "return_loss"])) or 0,
        "mature_contribution_margin_minor": _money_minor(_first_money(mature, ["mature_outcome", "mature_contribution_margin"])),
    }
    return components


def _missing_components(components: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
    missing = []
    if components.get("item_cost_minor") is None:
        missing.append("item_cost_minor")
    if snapshot.get("current_state") == "mature":
        for key in [
            "gross_negotiated_revenue_minor",
            "platform_payment_fees_minor",
            "mature_contribution_margin_minor",
        ]:
            if components.get(key) is None:
                missing.append(key)
    return missing


def _first_transition(events: list[dict[str, Any]], transition: str) -> dict[str, Any] | None:
    return next((event for event in events if event.get("transition_to") == transition), None)


def _last_transition(events: list[dict[str, Any]], transition: str) -> dict[str, Any] | None:
    matches = [event for event in events if event.get("transition_to") == transition]
    return matches[-1] if matches else None


def _first_money(value: Any, path: list[str | int]) -> dict[str, Any] | None:
    if isinstance(value, list):
        for item in value:
            found = _first_money(item, path)
            if found is not None:
                return found
        return None
    current = value
    for part in path:
        if isinstance(part, int):
            if not isinstance(current, list) or len(current) <= part:
                return None
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
    return current if isinstance(current, dict) and "amount_minor" in current else None


def _money_minor(value: dict[str, Any] | None) -> int | None:
    if value is None:
        return None
    return int(value["amount_minor"])


def _refund_amount_minor(events: list[dict[str, Any]]) -> int:
    return sum(
        _money_minor(_first_money(event, ["economics", "refund_amount"])) or 0
        for event in events
        if event.get("transition_to") in {"partially_refunded", "fully_refunded"}
    )


def _shipping_discount_minor(events: list[dict[str, Any]]) -> int:
    total = 0
    for event in events:
        for discount in event.get("discounts", []) or []:
            if isinstance(discount, dict) and discount.get("type") == "shipping":
                total += _money_minor(discount.get("amount")) or 0
    return total


def _event_number(events: list[dict[str, Any]], key: str) -> int | None:
    for event in events:
        for container in [event.get("economics"), event.get("mature_outcome")]:
            if isinstance(container, dict) and key in container and isinstance(container[key], (int, float)):
                return int(container[key])
    return None


def _currency(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        if isinstance(event.get("currency"), str):
            return event["currency"]
    return None


def _single_currency(transactions: list[dict[str, Any]]) -> str | None:
    currencies = {item["financial_components"].get("currency") for item in transactions if item["financial_components"].get("currency")}
    return currencies.pop() if len(currencies) == 1 else None


def _sku(event: dict[str, Any]) -> str:
    line_items = event.get("line_items") if isinstance(event, dict) else None
    if isinstance(line_items, list) and line_items and isinstance(line_items[0], dict):
        return str(line_items[0].get("sku") or "unknown")
    return "unknown"


def _inventory_age_bucket(age: int | None) -> str:
    if age is None:
        return "unknown"
    if age < 15:
        return "0-14"
    if age < 31:
        return "15-30"
    if age < 61:
        return "31-60"
    if age < 91:
        return "61-90"
    return "91+"


def _ratio_bucket(buyer_offer: int | None, asking: int | None) -> str:
    if buyer_offer is None or asking is None or asking <= 0:
        return "unknown"
    ratio = buyer_offer / asking
    if ratio < 0.5:
        return "<50%"
    if ratio < 0.7:
        return "50-69%"
    if ratio < 0.85:
        return "70-84%"
    if ratio < 1.0:
        return "85-99%"
    return "100%+"
