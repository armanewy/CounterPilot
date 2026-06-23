from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any

from behavior_lab.core import parse_time, stable_hash, to_jsonable, utc_now
from behavior_lab.ledger import ImmutableLedger


COUNTERPILOT_STATE_SCHEMA_VERSION = "counterpilot.transaction_event.v1"
COUNTERPILOT_STATE_RECORD_TYPE = "counterpilot_transaction_event"

STATES = {
    "offer_submitted",
    "offer_expired",
    "merchant_accepted",
    "merchant_declined",
    "merchant_countered",
    "buyer_countered",
    "buyer_accepted",
    "buyer_declined",
    "checkout_created",
    "checkout_expired",
    "order_created",
    "payment_pending",
    "paid",
    "cancelled",
    "partially_refunded",
    "fully_refunded",
    "return_opened",
    "return_received",
    "return_closed",
    "mature",
}

VALID_TRANSITIONS: dict[str | None, set[str]] = {
    None: {"offer_submitted"},
    "offer_submitted": {"offer_expired", "merchant_accepted", "merchant_declined", "merchant_countered", "buyer_countered", "cancelled"},
    "merchant_countered": {"offer_expired", "buyer_accepted", "buyer_declined", "buyer_countered", "cancelled"},
    "buyer_countered": {"offer_expired", "merchant_accepted", "merchant_declined", "merchant_countered", "cancelled"},
    "merchant_accepted": {"checkout_created", "cancelled"},
    "buyer_accepted": {"checkout_created", "cancelled"},
    "checkout_created": {"checkout_expired", "order_created", "payment_pending", "cancelled"},
    "order_created": {"payment_pending", "paid", "cancelled"},
    "payment_pending": {"paid", "cancelled"},
    "paid": {"partially_refunded", "fully_refunded", "return_opened", "mature"},
    "partially_refunded": {"fully_refunded", "return_opened", "mature"},
    "fully_refunded": {"return_opened", "mature"},
    "return_opened": {"return_received", "return_closed"},
    "return_received": {"return_closed"},
    "return_closed": {"return_opened", "mature"},
    "offer_expired": set(),
    "merchant_declined": set(),
    "buyer_declined": set(),
    "checkout_expired": set(),
    "cancelled": set(),
    "mature": set(),
}

MERCHANT_ACTION_STATES = {"merchant_accepted", "merchant_declined", "merchant_countered"}
SYSTEM_ACTION_STATES = {"checkout_created", "checkout_expired", "cancelled"}
ACTION_STATES = MERCHANT_ACTION_STATES | SYSTEM_ACTION_STATES
WEBHOOK_STATES = {
    "order_created",
    "payment_pending",
    "paid",
    "cancelled",
    "partially_refunded",
    "fully_refunded",
    "return_opened",
    "return_received",
    "return_closed",
}

TERMINAL_STATES = {"offer_expired", "merchant_declined", "buyer_declined", "checkout_expired", "cancelled", "mature"}

PII_KEY_TOKENS = {
    "name",
    "email",
    "phone",
    "address",
    "message",
    "note",
    "notes",
    "comment",
    "memo",
    "buyer_message",
    "customer_message",
}
PII_VALUE_PATTERNS = [
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(\d{3}\)\s*|\d{3}[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(
        r"\b\d{1,6}\s+[A-Z0-9.'-]+(?:\s+[A-Z0-9.'-]+){0,4}\s+"
        r"(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|way|court|ct|place|pl)\b",
        re.IGNORECASE,
    ),
]


class CounterpilotStateError(ValueError):
    pass


@dataclass(frozen=True)
class AppendResult:
    imported: bool
    idempotent_replay: bool
    transaction_id: str
    merchant_namespace: str
    current_state: str | None
    pending_event_ids: list[str]


def money(amount_minor: int, currency: str = "USD") -> dict[str, Any]:
    return {"amount_minor": int(amount_minor), "currency": currency}


class TransactionStateMachine:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.ledger = ImmutableLedger(self.data_dir / "counterpilot_transactions.jsonl")

    def append_event(self, event: dict[str, Any]) -> AppendResult:
        prepared = _normalize_event(dict(event))
        key = _transaction_key(prepared)
        record_id = _record_id(prepared)
        replayed = False
        imported = False
        with self.ledger.exclusive():
            records = self.ledger._scan_unlocked()
            self.ledger._verify_records(records)
            existing = next(
                (
                    record
                    for record in records
                    if record.get("record_type") == COUNTERPILOT_STATE_RECORD_TYPE
                    and record.get("record_id") == record_id
                ),
                None,
            )
            if existing is not None:
                if existing.get("payload") != prepared:
                    raise CounterpilotStateError(f"idempotency key already used for a different event: {prepared['idempotency_key']}")
                replayed = True
            else:
                existing_events = [
                    record["payload"]
                    for record in records
                    if record.get("record_type") == COUNTERPILOT_STATE_RECORD_TYPE
                    and record["payload"].get("merchant_namespace") == key[0]
                    and record["payload"].get("transaction_id") == key[1]
                ]
                same_event = [event for event in existing_events if event.get("event_id") == prepared["event_id"]]
                if same_event:
                    if same_event[0] == prepared:
                        replayed = True
                    else:
                        raise CounterpilotStateError(f"event_id already exists with different payload: {prepared['event_id']}")
                else:
                    self._validate_new_event_against_history(prepared, existing_events)
                    _append_prelocked(self.ledger, records, COUNTERPILOT_STATE_RECORD_TYPE, prepared, record_id)
                    imported = True
        snapshot = self.inspect(prepared["merchant_namespace"], prepared["transaction_id"])
        return AppendResult(imported, replayed, key[1], key[0], snapshot["current_state"], snapshot["pending_event_ids"])

    def inspect(self, merchant_namespace: str, transaction_id: str) -> dict[str, Any]:
        events = self._events_for(merchant_namespace, transaction_id)
        replay = _replay(events)
        return {
            "schema_version": "counterpilot.transaction_snapshot.v1",
            "merchant_namespace": merchant_namespace,
            "transaction_id": transaction_id,
            "current_state": replay["state"],
            "applied_event_ids": [event["event_id"] for event in replay["applied_events"]],
            "pending_event_ids": [item["event"]["event_id"] for item in replay["pending"]],
            "pending": replay["pending"],
            "errors": replay["errors"],
            "event_count": len(events),
            "available_actions": replay["available_actions"],
            "recommendations": replay["recommendations"],
            "merchant_decisions": replay["merchant_decisions"],
            "executed_actions": replay["executed_actions"],
            "currency": replay["currency"],
            "mature_outcome": replay["mature_outcome"],
        }

    def _events_for(self, merchant_namespace: str, transaction_id: str) -> list[dict[str, Any]]:
        return [
            event
            for event in self.ledger.payloads(COUNTERPILOT_STATE_RECORD_TYPE)
            if event.get("merchant_namespace") == merchant_namespace and event.get("transaction_id") == transaction_id
        ]

    def _validate_new_event_against_history(self, event: dict[str, Any], existing_events: list[dict[str, Any]]) -> None:
        replay = _replay(existing_events + [event])
        event_id = event["event_id"]
        event_errors = [error for error in replay["errors"] if error.get("event_id") == event_id]
        event_pending = [pending for pending in replay["pending"] if pending["event"]["event_id"] == event_id]
        if not event_errors and not event_pending:
            return
        if event["transition_to"] in WEBHOOK_STATES and event_pending and not event_errors:
            return
        details = event_errors or event_pending
        raise CounterpilotStateError(f"invalid transition event {event_id}: {details}")


def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    required = [
        "event_id",
        "merchant_namespace",
        "transaction_id",
        "occurred_at",
        "received_at",
        "source",
        "schema_version",
        "idempotency_key",
        "transition_to",
    ]
    missing = [field for field in required if not isinstance(event.get(field), str) or not event[field].strip()]
    if missing:
        raise CounterpilotStateError(f"transition event missing required fields: {missing}")
    if event["schema_version"] != COUNTERPILOT_STATE_SCHEMA_VERSION:
        raise CounterpilotStateError(f"schema_version must be {COUNTERPILOT_STATE_SCHEMA_VERSION!r}")
    if event["transition_to"] not in STATES:
        raise CounterpilotStateError(f"unknown transition_to: {event['transition_to']!r}")
    parse_time(event["occurred_at"])
    parse_time(event["received_at"])
    _reject_pii(event)
    if event["transition_to"] in ACTION_STATES:
        _validate_action_fields(event)
    _validate_money_and_discounts(event)
    if event["transition_to"] == "mature":
        _validate_mature_outcome(event)
    event["event_hash"] = stable_hash({key: value for key, value in event.items() if key != "event_hash"})
    return event


def _validate_action_fields(event: dict[str, Any]) -> None:
    if not isinstance(event.get("available_actions"), list) or not event["available_actions"]:
        raise CounterpilotStateError("merchant/system actions require available_actions recorded before action")
    if "recommendation" not in event or "merchant_decision" not in event or "executed_action" not in event:
        raise CounterpilotStateError("actions must separate recommendation, merchant_decision, and executed_action")
    available = {str(action.get("action")) for action in event["available_actions"] if isinstance(action, dict)}
    executed = event.get("executed_action")
    if not isinstance(executed, dict) or str(executed.get("action")) not in available:
        raise CounterpilotStateError("executed_action must be one of available_actions")


def _validate_money_and_discounts(event: dict[str, Any]) -> None:
    currency = event.get("currency")
    if currency is not None and (not isinstance(currency, str) or len(currency) != 3):
        raise CounterpilotStateError("currency must be a 3-letter ISO code")
    money_values = _money_values(event)
    currencies = {value["currency"] for value in money_values}
    if currency:
        currencies.add(currency)
    if len(currencies) > 1 and not _has_conversion(event, currencies):
        raise CounterpilotStateError(f"mixed-currency arithmetic requires explicit conversion record: {sorted(currencies)}")
    for item in event.get("line_items", []) or []:
        if not isinstance(item, dict) or int(item.get("quantity", 0)) <= 0:
            raise CounterpilotStateError("line_items require positive quantity")
    for discount in event.get("discounts", []) or []:
        if not isinstance(discount, dict) or discount.get("type") not in {"shipping", "item", "order"}:
            raise CounterpilotStateError("discount type must be shipping, item, or order")
        _require_money(discount.get("amount"), "discount.amount")
    shipping_discounts = [item for item in event.get("discounts", []) or [] if item.get("type") == "shipping"]
    if shipping_discounts:
        shipping_cost = _find_money(event, "shipping_cost")
        if shipping_cost is None or int(shipping_cost["amount_minor"]) <= 0:
            raise CounterpilotStateError("shipping discounts require a positive shipping_cost; free shipping is still a merchant cost")


def _validate_mature_outcome(event: dict[str, Any]) -> None:
    outcome = event.get("mature_outcome")
    if not isinstance(outcome, dict):
        raise CounterpilotStateError("mature transition requires mature_outcome")
    required = [
        "payment_resolution",
        "refund_return_maturity_date",
        "reconciled_fees",
        "reconciled_fulfillment_costs",
        "mature_contribution_margin",
    ]
    missing = [field for field in required if field not in outcome]
    if missing:
        raise CounterpilotStateError(f"mature_outcome missing fields: {missing}")
    if outcome["payment_resolution"] not in {"paid", "cancelled", "refunded", "partially_refunded"}:
        raise CounterpilotStateError("mature_outcome.payment_resolution is invalid")
    parse_time(str(outcome["refund_return_maturity_date"]))
    for field in ["reconciled_fees", "reconciled_fulfillment_costs", "mature_contribution_margin"]:
        _require_money(outcome[field], f"mature_outcome.{field}")


def _replay(events: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(events, key=lambda event: (parse_time(event["occurred_at"]), parse_time(event["received_at"]), event["event_id"]))
    state: str | None = None
    currency: str | None = None
    applied: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    available_actions: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    merchant_decisions: list[dict[str, Any]] = []
    executed_actions: list[dict[str, Any]] = []
    mature_outcome: dict[str, Any] | None = None
    for event in ordered:
        transition = event["transition_to"]
        event_currency = _event_currency(event)
        if currency is None and event_currency is not None:
            currency = event_currency
        if currency is not None and event_currency is not None and event_currency != currency and not _has_conversion(event, {currency, event_currency}):
            errors.append({"event_id": event["event_id"], "reason": "currency_mismatch", "state": state})
            continue
        allowed = VALID_TRANSITIONS.get(state, set())
        if transition not in allowed:
            reason = "missing_predecessor" if _could_be_future_transition(transition, state) else "invalid_transition"
            item = {"event": event, "reason": reason, "state": state}
            if transition in WEBHOOK_STATES and reason == "missing_predecessor":
                pending.append(item)
                continue
            errors.append({"event_id": event["event_id"], "reason": reason, "state": state, "transition_to": transition})
            continue
        state = transition
        applied.append(event)
        if isinstance(event.get("available_actions"), list):
            available_actions = event["available_actions"]
        if isinstance(event.get("recommendation"), dict):
            recommendations.append(event["recommendation"])
        if isinstance(event.get("merchant_decision"), dict):
            merchant_decisions.append(event["merchant_decision"])
        if isinstance(event.get("executed_action"), dict):
            executed_actions.append(event["executed_action"])
        if transition == "mature":
            mature_outcome = event["mature_outcome"]
    return {
        "state": state,
        "currency": currency,
        "applied_events": applied,
        "pending": pending,
        "errors": errors,
        "available_actions": available_actions,
        "recommendations": recommendations,
        "merchant_decisions": merchant_decisions,
        "executed_actions": executed_actions,
        "mature_outcome": mature_outcome,
    }


def _could_be_future_transition(transition: str, state: str | None) -> bool:
    if state in TERMINAL_STATES:
        return False
    reachable = set(VALID_TRANSITIONS.get(state, set()))
    frontier = list(reachable)
    while frontier:
        item = frontier.pop()
        if item == transition:
            return True
        for next_state in VALID_TRANSITIONS.get(item, set()):
            if next_state not in reachable:
                reachable.add(next_state)
                frontier.append(next_state)
    return False


def _transaction_key(event: dict[str, Any]) -> tuple[str, str]:
    return (event["merchant_namespace"], event["transaction_id"])


def _record_id(event: dict[str, Any]) -> str:
    return "counterpilot_state_" + stable_hash(
        {
            "merchant_namespace": event["merchant_namespace"],
            "transaction_id": event["transaction_id"],
            "idempotency_key": event["idempotency_key"],
        }
    )


def _append_prelocked(
    ledger: ImmutableLedger,
    records: list[dict[str, Any]],
    record_type: str,
    payload: dict[str, Any],
    record_id: str,
) -> None:
    if any(record.get("record_id") == record_id for record in records):
        raise CounterpilotStateError(f"record_id already exists: {record_id}")
    previous = str(records[-1]["record_hash"]) if records else ledger.genesis_hash
    body = {
        "record_id": record_id,
        "record_type": record_type,
        "written_at": utc_now(),
        "previous_hash": previous,
        "payload": to_jsonable(payload),
    }
    body["record_hash"] = stable_hash(body)
    with ledger.path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(body, sort_keys=True, ensure_ascii=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _money_values(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "amount_minor" in value and "currency" in value:
            found.append(_require_money(value, "money"))
        for item in value.values():
            found.extend(_money_values(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_money_values(item))
    return found


def _require_money(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CounterpilotStateError(f"{field} must be a money object")
    amount = value.get("amount_minor")
    currency = value.get("currency")
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise CounterpilotStateError(f"{field}.amount_minor must be an integer")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
        raise CounterpilotStateError(f"{field}.currency must be a 3-letter ISO code")
    return {"amount_minor": amount, "currency": currency.upper()}


def _find_money(value: Any, key: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if key in value:
            return _require_money(value[key], key)
        for item in value.values():
            found = _find_money(item, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_money(item, key)
            if found is not None:
                return found
    return None


def _event_currency(event: dict[str, Any]) -> str | None:
    if isinstance(event.get("currency"), str):
        return event["currency"].upper()
    values = _money_values(event)
    if not values:
        return None
    currencies = {value["currency"] for value in values}
    return sorted(currencies)[0] if len(currencies) == 1 else None


def _has_conversion(event: dict[str, Any], currencies: set[str]) -> bool:
    conversion = event.get("currency_conversion")
    if not isinstance(conversion, dict):
        return False
    from_currency = str(conversion.get("from_currency", "")).upper()
    to_currency = str(conversion.get("to_currency", "")).upper()
    if {from_currency, to_currency} != {currency.upper() for currency in currencies}:
        return False
    return conversion.get("rate_basis") is not None and isinstance(conversion.get("source"), str)


def _reject_pii(value: Any, *, path: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            tokens = _tokens(key_text)
            if tokens & PII_KEY_TOKENS:
                raise CounterpilotStateError(f"PII/free-form buyer field is not allowed: {path + key_text}")
            _reject_pii(item, path=f"{path}{key_text}.")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_pii(item, path=f"{path}{index}.")
    elif isinstance(value, str) and any(pattern.search(value) for pattern in PII_VALUE_PATTERNS):
        raise CounterpilotStateError(f"PII value is not allowed at {path.rstrip('.')}")


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    for separator in [".", "-", ":", "/", "\\"]:
        lowered = lowered.replace(separator, "_")
    return {token for token in lowered.split("_") if token}
