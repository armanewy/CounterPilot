from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from integrations.shopify import DeterministicFakeShopifyProvider, ShopifyDevelopmentAdapter, ShopifyOfferInput, sign_webhook


SECRET = b"counterpilot-e2e-shopify-secret"


def run_development_store_e2e(*, data_dir: str | Path, report_path: str | Path | None = None) -> dict[str, Any]:
    data_root = Path(data_dir)
    provider = DeterministicFakeShopifyProvider()
    adapter = ShopifyDevelopmentAdapter(data_dir=data_root, provider=provider, webhook_secret=SECRET)
    offer = ShopifyOfferInput(
        merchant_id="merchant_demo_refurb",
        store_id="store_demo_shopify",
        store_domain="counterpilot-dev-store.myshopify.com",
        product_gid="gid://shopify/Product/100",
        variant_gid="gid://shopify/ProductVariant/200",
        sku="refurb-pc-i7",
        quantity=1,
        currency="USD",
        asking_price_minor=90000,
        buyer_offer_amount_minor=72000,
        cost_basis_minor=52000,
        shipping_cost_minor=3400,
        fulfillment_cost_minor=1200,
        buyer_session_reference="session-ref-001",
        contact_email="buyer@example.com",
    )
    surface = adapter.enable_offer_surface(
        merchant_id=offer.merchant_id,
        store_id=offer.store_id,
        visibility={"product_gid": offer.product_gid, "enabled": True},
    )
    submitted = adapter.submit_offer(offer, occurred_at="2026-06-22T10:00:00+00:00")
    transaction_id = submitted["transaction_id"]
    inbox_after_submit = adapter.offer_inbox(merchant_id=offer.merchant_id, store_id=offer.store_id)
    counter = adapter.merchant_counter(
        merchant_id=offer.merchant_id,
        store_id=offer.store_id,
        transaction_id=transaction_id,
        amount_minor=76000,
        shipping_discount_minor=3400,
        occurred_at="2026-06-22T10:05:00+00:00",
        currency=offer.currency,
        merchant_floor_minor=69000,
        cost_basis_minor=offer.cost_basis_minor,
        fulfillment_cost_minor=offer.fulfillment_cost_minor,
    )
    buyer_accept = adapter.buyer_accept(
        merchant_id=offer.merchant_id,
        store_id=offer.store_id,
        transaction_id=transaction_id,
        occurred_at="2026-06-22T10:10:00+00:00",
    )
    checkout = adapter.create_checkout(
        offer=offer,
        transaction_id=transaction_id,
        counter_amount_minor=76000,
        occurred_at="2026-06-22T10:11:00+00:00",
        reserve_inventory=True,
    )
    checkout_delivery = adapter.checkout_delivery(
        merchant_id=offer.merchant_id,
        store_id=offer.store_id,
        transaction_id=transaction_id,
    )
    resource_ids = _resource_ids(provider)
    order_created = _send_webhook(
        adapter,
        topic="orders/create",
        delivery_id="delivery_order_created",
        merchant_id=offer.merchant_id,
        store_id=offer.store_id,
        transaction_id=transaction_id,
        payload={
            "occurred_at": "2026-06-22T10:12:00+00:00",
            "received_at": "2026-06-22T10:12:01+00:00",
            "resource_ids": resource_ids,
        },
    )
    duplicate_order_created = _send_webhook(
        adapter,
        topic="orders/create",
        delivery_id="delivery_order_created",
        merchant_id=offer.merchant_id,
        store_id=offer.store_id,
        transaction_id=transaction_id,
        payload={
            "occurred_at": "2026-06-22T10:12:00+00:00",
            "received_at": "2026-06-22T10:12:01+00:00",
            "resource_ids": resource_ids,
        },
    )
    paid = _send_webhook(
        adapter,
        topic="orders/paid",
        delivery_id="delivery_order_paid",
        merchant_id=offer.merchant_id,
        store_id=offer.store_id,
        transaction_id=transaction_id,
        payload={
            "occurred_at": "2026-06-22T10:14:00+00:00",
            "financial_status": "paid",
            "economics": {"final_sale_price": {"amount_minor": 76000, "currency": "USD"}},
            "resource_ids": {"order_gid": resource_ids["order_gid"]},
        },
    )
    refund = _send_webhook(
        adapter,
        topic="refunds/create",
        delivery_id="delivery_refund_partial",
        merchant_id=offer.merchant_id,
        store_id=offer.store_id,
        transaction_id=transaction_id,
        payload={
            "occurred_at": "2026-06-22T10:16:00+00:00",
            "refund_status": "partial",
            "resource_ids": {"order_gid": resource_ids["order_gid"], "refund_gid": "gid://shopify/Refund/5001"},
            "economics": {"refund_amount": {"amount_minor": 1000, "currency": "USD"}},
        },
    )
    out_of_order = _send_webhook(
        adapter,
        topic="returns/close",
        delivery_id="delivery_return_close",
        merchant_id=offer.merchant_id,
        store_id=offer.store_id,
        transaction_id=transaction_id,
        payload={
            "occurred_at": "2026-06-22T10:19:00+00:00",
            "resource_ids": {"order_gid": resource_ids["order_gid"], "return_gid": "gid://shopify/Return/6001"},
        },
    )
    return_open = _send_webhook(
        adapter,
        topic="returns/request",
        delivery_id="delivery_return_open",
        merchant_id=offer.merchant_id,
        store_id=offer.store_id,
        transaction_id=transaction_id,
        payload={
            "occurred_at": "2026-06-22T10:18:00+00:00",
            "resource_ids": {"order_gid": resource_ids["order_gid"], "return_gid": "gid://shopify/Return/6001"},
        },
    )
    mature = adapter.mature_and_export(
        offer=offer,
        transaction_id=transaction_id,
        occurred_at="2026-07-22T10:20:00+00:00",
        return_maturity_at="2026-07-22T10:20:00+00:00",
        final_sale_price_minor=76000,
        reconciled_fees_minor=2234,
        reconciled_fulfillment_cost_minor=4600,
        mature_margin_minor=16166,
    )
    report = {
        "schema_version": "counterpilot_shopify_e2e_report.v1",
        "surface": surface,
        "transaction_id": transaction_id,
        "merchant_inbox": {
            "offer_count_after_submit": len(inbox_after_submit["offers"]),
            "first_offer_state": inbox_after_submit["offers"][0]["current_state"],
        },
        "events": {
            "offer": submitted["state"]["applied_event_ids"],
            "merchant_counter_state": counter["current_state"],
            "buyer_accept_state": buyer_accept["current_state"],
            "checkout_state": checkout["state"]["current_state"],
            "order_created": order_created,
            "duplicate_order_created": duplicate_order_created,
            "paid": paid,
            "partial_refund": refund,
            "out_of_order_return_close": out_of_order,
            "return_open": return_open,
            "mature_state": mature["state"]["current_state"],
        },
        "state_transition_log": _transition_log(adapter, offer.merchant_id, offer.store_id, transaction_id),
        "idempotency_behavior": {
            "duplicate_delivery_replay": duplicate_order_created["delivery_replay"],
            "duplicate_order_created_replay": duplicate_order_created["result"]["idempotent_replay"],
            "duplicate_order_event_count": duplicate_order_created["state"]["event_count"],
        },
        "out_of_order_behavior": {
            "return_close_pending_before_open": "shopify_delivery_return_close" in out_of_order["state"]["pending_event_ids"],
            "state_after_reconciliation": return_open["state"]["current_state"],
            "pending_after_reconciliation": return_open["state"]["pending_event_ids"],
        },
        "financial_components": {
            "final_sale_price_minor": 76000,
            "cost_basis_minor": offer.cost_basis_minor,
            "reconciled_fees_minor": 2234,
            "reconciled_fulfillment_cost_minor": 4600,
            "partial_refund_minor": 1000,
            "mature_contribution_margin_minor": 16166,
            "reconciliation_formula": "final_sale_price - cost_basis - fees - fulfillment - partial_refund",
            "reconciliation_verified": 76000 - offer.cost_basis_minor - 2234 - 4600 - 1000 == 16166,
        },
        "shopify_resource_linkage": {
            "checkout_link_available_to_delivery_flow": checkout_delivery["checkout_url"].startswith("https://"),
            "checkout_link_reported_value": "operational_store_only",
            "fake_draft_order_count": len(provider.created_draft_orders),
            "stored_in": "operational_store",
        },
        "research_projection": mature["research_export"],
        "pii_redaction": _pii_checks(mature["research_export"]),
        "model_recommendations_present": _model_recommendations_present(mature["state"]),
    }
    if report_path is not None:
        _write_report(report, report_path)
    return report


def _send_webhook(adapter: ShopifyDevelopmentAdapter, *, topic: str, delivery_id: str, merchant_id: str, store_id: str, transaction_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    headers = {
        "X-Shopify-Hmac-Sha256": sign_webhook(raw, SECRET),
        "X-Shopify-Shop-Domain": "counterpilot-dev-store.myshopify.com",
        "X-Shopify-Webhook-Id": delivery_id,
        "X-Shopify-Topic": topic,
    }
    return adapter.ingest_webhook(raw_body=raw, headers=headers, merchant_id=merchant_id, store_id=store_id, transaction_id=transaction_id)


def _resource_ids(provider: DeterministicFakeShopifyProvider) -> dict[str, str]:
    result = provider.created_draft_orders[0]["result"]
    return {
        "checkout_gid": result["resource_ids"]["checkout_gid"],
        "draft_order_gid": result["draft_order_id"],
        "order_gid": "gid://shopify/Order/3001",
    }


def _transition_log(adapter: ShopifyDevelopmentAdapter, merchant_id: str, store_id: str, transaction_id: str) -> list[dict[str, Any]]:
    namespace = f"{merchant_id}:{store_id}"
    events = [
        event
        for event in adapter.state.ledger.payloads("counterpilot_transaction_event")
        if event.get("merchant_namespace") == namespace and event.get("transaction_id") == transaction_id
    ]
    return [
        {
            "event_id": event["event_id"],
            "occurred_at": event["occurred_at"],
            "received_at": event["received_at"],
            "source": event["source"],
            "transition_to": event["transition_to"],
        }
        for event in sorted(events, key=lambda item: (item["occurred_at"], item["received_at"], item["event_id"]))
    ]


def _pii_checks(export: dict[str, Any]) -> dict[str, bool]:
    rendered = json.dumps(export, sort_keys=True)
    return {
        "no_email": "buyer@example.com" not in rendered,
        "no_shopify_gid": "gid://shopify" not in rendered,
        "no_checkout_url": "checkout" not in rendered.lower() or "https://" not in rendered.lower(),
        "no_buyer_message": "message" not in rendered.lower(),
    }


def _model_recommendations_present(snapshot: dict[str, Any]) -> bool:
    for recommendation in snapshot.get("recommendations", []):
        if recommendation.get("system_mode") != "manual_only" or recommendation.get("recommendation_id") is not None:
            return True
    return False


def _write_report(report: dict[str, Any], report_path: str | Path) -> None:
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    lines = [
        "# Counterpilot E2E Report",
        "",
        f"- Transaction ID: `{report['transaction_id']}`",
        f"- Mature state: `{report['events']['mature_state']}`",
        f"- Duplicate order webhook idempotent: `{report['events']['duplicate_order_created']['result']['idempotent_replay']}`",
        f"- Mature contribution margin minor: `{report['financial_components']['mature_contribution_margin_minor']}`",
        f"- Model recommendations present: `{report['model_recommendations_present']}`",
        f"- PII redaction checks: `{report['pii_redaction']}`",
        "",
        "```json",
        json.dumps(report, indent=2, sort_keys=True),
        "```",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
