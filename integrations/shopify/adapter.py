from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from behavior_lab.core import stable_hash
from behavior_lab.marginpilot_core import consent_grant, research_export
from behavior_lab.marginpilot_state import MARGINPILOT_STATE_SCHEMA_VERSION, TransactionStateMachine, money
from behavior_lab.marginpilot_storage import (
    MERCHANT_SPECIFIC_MODEL_TRAINING,
    ConsentLedger,
    EphemeralMappingLayer,
    LocalFileEncryptedAtRestAdapter,
    OperationalTransactionRecord,
    OperationalTransactionStore,
    ResearchEventRecord,
    ResearchEventStore,
)
from integrations.shopify.provider import DraftOrderRequest, ShopifyProvider
from integrations.shopify.token_store import ShopifyTokenRecord, ShopifyTokenStore
from integrations.shopify.webhooks import ShopifyWebhookError, verify_webhook_hmac


SHOPIFY_ADAPTER_SCHEMA_VERSION = "marginpilot.shopify_adapter.v1"


@dataclass(frozen=True)
class ShopifyOfferInput:
    merchant_id: str
    store_id: str
    store_domain: str
    product_gid: str
    variant_gid: str
    sku: str
    quantity: int
    currency: str
    asking_price_minor: int
    buyer_offer_amount_minor: int
    cost_basis_minor: int
    shipping_cost_minor: int
    fulfillment_cost_minor: int
    buyer_session_reference: str
    contact_email: str | None = None


class ShopifyDevelopmentAdapter:
    def __init__(
        self,
        *,
        data_dir: str | Path,
        provider: ShopifyProvider,
        webhook_secret: bytes | str,
    ):
        self.data_dir = Path(data_dir)
        self.provider = provider
        self.webhook_secret = webhook_secret
        self.state = TransactionStateMachine(self.data_dir)
        self.operational = OperationalTransactionStore(LocalFileEncryptedAtRestAdapter(self.data_dir / "operational_encrypted"))
        self.consent = ConsentLedger(self.data_dir / "consent.jsonl")
        self.research = ResearchEventStore(self.data_dir / "research_events.jsonl", consent_ledger=self.consent)
        self.tokens = ShopifyTokenStore.local(self.data_dir)

    def install_app_token(
        self,
        *,
        merchant_id: str,
        store_id: str,
        store_domain: str,
        access_token: str,
        scopes: tuple[str, ...] | list[str],
        installed_at: str,
    ) -> dict[str, Any]:
        record = ShopifyTokenRecord(
            merchant_id=merchant_id,
            store_id=store_id,
            store_domain=store_domain,
            access_token=access_token,
            scopes=tuple(scopes),
            installed_at=installed_at,
            provenance={"source": "shopify_oauth_install"},
        )
        return {
            "merchant_id": merchant_id,
            "record_id": self.tokens.put(record),
            "scope_count": len(record.scopes),
            "store_id": store_id,
        }

    def enable_offer_surface(self, *, merchant_id: str, store_id: str, visibility: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": SHOPIFY_ADAPTER_SCHEMA_VERSION,
            "merchant_id": merchant_id,
            "store_id": store_id,
            "surface": "theme_app_extension_product_block",
            "visibility": dict(visibility),
            "network_calls": 0,
        }

    def offer_inbox(self, *, merchant_id: str, store_id: str) -> dict[str, Any]:
        namespace = _namespace(merchant_id, store_id)
        transaction_ids = sorted(
            {
                event["transaction_id"]
                for event in self.state.ledger.payloads("marginpilot_transaction_event")
                if event.get("merchant_namespace") == namespace
            }
        )
        rows = []
        for transaction_id in transaction_ids:
            snapshot = self.state.inspect(namespace, transaction_id)
            rows.append(
                {
                    "available_actions": snapshot["available_actions"],
                    "current_state": snapshot["current_state"],
                    "pending_event_ids": snapshot["pending_event_ids"],
                    "transaction_id": transaction_id,
                }
            )
        return {"schema_version": "marginpilot.shopify_offer_inbox.v1", "merchant_namespace": namespace, "offers": rows}

    def submit_offer(self, offer: ShopifyOfferInput, *, occurred_at: str) -> dict[str, Any]:
        _validate_offer_input(offer)
        transaction_id = "mp_txn_" + stable_hash(
            {
                "merchant_id": offer.merchant_id,
                "store_id": offer.store_id,
                "buyer_session_reference": offer.buyer_session_reference,
                "variant_gid": offer.variant_gid,
                "occurred_at": occurred_at,
            }
        )[:16]
        namespace = _namespace(offer.merchant_id, offer.store_id)
        operational = OperationalTransactionRecord(
            merchant_id=offer.merchant_id,
            store_id=offer.store_id,
            operational_transaction_id=transaction_id,
            shopify_resource_ids={
                "product_gid": offer.product_gid,
                "variant_gid": offer.variant_gid,
            },
            contact_delivery_reference=f"offer_contact_ref_{transaction_id}",
            checkout_url_reference=f"pending_checkout_{transaction_id}",
            fulfillment_state="not_started",
            payment_state="not_started",
            retention_policy="delete_customer_data_after_return_window",
            retention_expires_at=None,
            operational_customer_data={"email": offer.contact_email} if offer.contact_email else {},
            provenance={"source": "shopify_offer_surface"},
        )
        self.operational.put(operational)
        event = _event(
            "offer_submitted",
            event_id=f"{transaction_id}_offer_submitted",
            namespace=namespace,
            transaction_id=transaction_id,
            occurred_at=occurred_at,
            source="shopify_theme_app_extension",
            currency=offer.currency,
            line_items=[{"sku": offer.sku, "quantity": offer.quantity, "unit_price": money(offer.asking_price_minor, offer.currency)}],
            economics={
                "buyer_offer": money(offer.buyer_offer_amount_minor, offer.currency),
                "shipping_cost": money(offer.shipping_cost_minor, offer.currency),
                "cost_basis": money(offer.cost_basis_minor, offer.currency),
                "fulfillment_cost": money(offer.fulfillment_cost_minor, offer.currency),
            },
        )
        self.state.append_event(event)
        return {"transaction_id": transaction_id, "merchant_namespace": namespace, "state": self.state.inspect(namespace, transaction_id)}

    def merchant_accept(self, *, merchant_id: str, store_id: str, transaction_id: str, occurred_at: str, merchant_floor_minor: int | None = None) -> dict[str, Any]:
        namespace = _namespace(merchant_id, store_id)
        self.state.append_event(
            _event(
                "merchant_accepted",
                event_id=f"{transaction_id}_merchant_accepted",
                namespace=namespace,
                transaction_id=transaction_id,
                occurred_at=occurred_at,
                source="embedded_admin",
                available_actions=[{"action": "accept"}, {"action": "counter"}, {"action": "decline"}, {"action": "expire"}],
                recommendation={"system_mode": "manual_only", "recommendation_id": None},
                merchant_decision={"action": "accept", "merchant_floor_minor": merchant_floor_minor},
                executed_action={"action": "accept"},
            )
        )
        return self.state.inspect(namespace, transaction_id)

    def merchant_decline(self, *, merchant_id: str, store_id: str, transaction_id: str, occurred_at: str) -> dict[str, Any]:
        namespace = _namespace(merchant_id, store_id)
        self.state.append_event(
            _event(
                "merchant_declined",
                event_id=f"{transaction_id}_merchant_declined",
                namespace=namespace,
                transaction_id=transaction_id,
                occurred_at=occurred_at,
                source="embedded_admin",
                available_actions=[{"action": "accept"}, {"action": "counter"}, {"action": "decline"}, {"action": "expire"}],
                recommendation={"system_mode": "manual_only", "recommendation_id": None},
                merchant_decision={"action": "decline"},
                executed_action={"action": "decline"},
            )
        )
        return self.state.inspect(namespace, transaction_id)

    def expire_offer(self, *, merchant_id: str, store_id: str, transaction_id: str, occurred_at: str) -> dict[str, Any]:
        namespace = _namespace(merchant_id, store_id)
        self.state.append_event(
            _event(
                "offer_expired",
                event_id=f"{transaction_id}_offer_expired",
                namespace=namespace,
                transaction_id=transaction_id,
                occurred_at=occurred_at,
                source="embedded_admin",
            )
        )
        return self.state.inspect(namespace, transaction_id)

    def merchant_counter(
        self,
        *,
        merchant_id: str,
        store_id: str,
        transaction_id: str,
        amount_minor: int,
        shipping_discount_minor: int,
        occurred_at: str,
        currency: str = "USD",
        merchant_floor_minor: int | None = None,
        cost_basis_minor: int | None = None,
        fulfillment_cost_minor: int | None = None,
    ) -> dict[str, Any]:
        namespace = _namespace(merchant_id, store_id)
        economics: dict[str, Any] = {"counter_amount": money(amount_minor, currency), "shipping_cost": money(shipping_discount_minor, currency)}
        if merchant_floor_minor is not None:
            economics["merchant_floor"] = money(merchant_floor_minor, currency)
        if cost_basis_minor is not None:
            economics["cost_basis"] = money(cost_basis_minor, currency)
        if fulfillment_cost_minor is not None:
            economics["fulfillment_cost"] = money(fulfillment_cost_minor, currency)
        event = _event(
            "merchant_countered",
            event_id=f"{transaction_id}_merchant_countered",
            namespace=namespace,
            transaction_id=transaction_id,
            occurred_at=occurred_at,
            source="embedded_admin",
            currency=currency,
            available_actions=[{"action": "counter"}, {"action": "accept"}, {"action": "decline"}, {"action": "expire"}],
            recommendation={"system_mode": "manual_only", "recommendation_id": None},
            merchant_decision={"action": "counter", "amount_minor": amount_minor, "merchant_floor_minor": merchant_floor_minor},
            executed_action={"action": "counter"},
            economics=economics,
            discounts=[{"type": "shipping", "amount": money(shipping_discount_minor, currency)}] if shipping_discount_minor else [],
        )
        self.state.append_event(event)
        return self.state.inspect(namespace, transaction_id)

    def buyer_accept(self, *, merchant_id: str, store_id: str, transaction_id: str, occurred_at: str) -> dict[str, Any]:
        namespace = _namespace(merchant_id, store_id)
        self.state.append_event(
            _event(
                "buyer_accepted",
                event_id=f"{transaction_id}_buyer_accepted",
                namespace=namespace,
                transaction_id=transaction_id,
                occurred_at=occurred_at,
                source="buyer_offer_surface",
            )
        )
        return self.state.inspect(namespace, transaction_id)

    def create_checkout(self, *, offer: ShopifyOfferInput, transaction_id: str, counter_amount_minor: int, occurred_at: str, reserve_inventory: bool = False) -> dict[str, Any]:
        namespace = _namespace(offer.merchant_id, offer.store_id)
        discount_minor = max(offer.asking_price_minor - counter_amount_minor, 0)
        draft = self.provider.create_discounted_draft_order(
            DraftOrderRequest(
                store_domain=offer.store_domain,
                transaction_id=transaction_id,
                line_items=[{"variant_gid": offer.variant_gid, "quantity": offer.quantity}],
                currency=offer.currency,
                negotiated_amount_minor=counter_amount_minor,
                shipping_cost_minor=offer.shipping_cost_minor,
                discount_minor=discount_minor,
                reserve_inventory=reserve_inventory,
            )
        )
        operational = OperationalTransactionRecord(
            merchant_id=offer.merchant_id,
            store_id=offer.store_id,
            operational_transaction_id=transaction_id,
            shopify_resource_ids={
                "product_gid": offer.product_gid,
                "variant_gid": offer.variant_gid,
                **draft.resource_ids,
            },
            contact_delivery_reference=f"checkout_contact_ref_{transaction_id}",
            checkout_url_reference=draft.invoice_url,
            fulfillment_state="not_started",
            payment_state="checkout_created",
            retention_policy="delete_customer_data_after_return_window",
            retention_expires_at=None,
            operational_customer_data={"email": offer.contact_email} if offer.contact_email else {},
            provenance={"source": "shopify_draft_order"},
        )
        self.operational.put(operational)
        event = _event(
            "checkout_created",
            event_id=f"{transaction_id}_checkout_created",
            namespace=namespace,
            transaction_id=transaction_id,
            occurred_at=occurred_at,
            source="shopify_admin_graphql",
            available_actions=[{"action": "create_checkout"}, {"action": "cancel"}],
            recommendation={"system_mode": "manual_only", "recommendation_id": None},
            merchant_decision={"action": "create_checkout"},
            executed_action={"action": "create_checkout"},
            checkout_reference={"kind": "draft_order_invoice"},
        )
        self.state.append_event(event)
        return {"draft_order": {"id_reference": "operational_store", "invoice_url_reference": "operational_store"}, "state": self.state.inspect(namespace, transaction_id)}

    def checkout_delivery(self, *, merchant_id: str, store_id: str, transaction_id: str) -> dict[str, Any]:
        operational = self.operational.get(merchant_id=merchant_id, store_id=store_id, operational_transaction_id=transaction_id)
        if operational is None:
            raise ShopifyWebhookError("missing operational checkout record")
        return {
            "schema_version": "marginpilot.shopify_checkout_delivery.v1",
            "contact_delivery_reference": operational.contact_delivery_reference,
            "checkout_url": operational.checkout_url_reference,
        }

    def ingest_webhook(self, *, raw_body: bytes, headers: Mapping[str, str], merchant_id: str, store_id: str, transaction_id: str) -> dict[str, Any]:
        delivery_id = verify_webhook_hmac(raw_body, headers, self.webhook_secret)
        payload = json.loads(raw_body.decode("utf-8"))
        topic = _header(headers, "X-Shopify-Topic") or str(payload.get("topic", ""))
        namespace = _namespace(merchant_id, store_id)
        transition = _topic_to_transition(topic, payload)
        event = _event(
            transition,
            event_id=f"shopify_{delivery_id}",
            namespace=namespace,
            transaction_id=transaction_id,
            occurred_at=str(payload["occurred_at"]),
            received_at=str(payload.get("received_at", payload["occurred_at"])),
            source="shopify_webhook",
            idempotency_key=delivery_id,
            economics=payload.get("economics", {}),
        )
        result = self.state.append_event(event)
        return {"delivery_id": delivery_id, "transition": transition, "result": result.__dict__, "state": self.state.inspect(namespace, transaction_id)}

    def ingest_app_webhook(self, *, raw_body: bytes, headers: Mapping[str, str], merchant_id: str, store_id: str) -> dict[str, Any]:
        delivery_id = verify_webhook_hmac(raw_body, headers, self.webhook_secret)
        payload = json.loads(raw_body.decode("utf-8"))
        topic = (_header(headers, "X-Shopify-Topic") or str(payload.get("topic", ""))).lower().replace(".", "/")
        if topic == "app/uninstalled":
            revoked = self.tokens.revoke(
                merchant_id=merchant_id,
                store_id=store_id,
                revoked_at=str(payload.get("occurred_at") or payload.get("received_at")),
                provenance={"source": "shopify_app_uninstalled", "delivery_id": delivery_id},
            )
            return {"delivery_id": delivery_id, "topic": topic, "action": "token_revoked", "result": revoked}
        if topic in {"customers/data_request", "customers/redact", "shop/redact"}:
            return {
                "delivery_id": delivery_id,
                "topic": topic,
                "action": "compliance_topic_received",
                "operational_redaction": "handled_by_retention_or_manual_customer_match",
            }
        raise ShopifyWebhookError(f"unsupported Shopify app webhook topic: {topic}")

    def mature_and_export(
        self,
        *,
        offer: ShopifyOfferInput,
        transaction_id: str,
        occurred_at: str,
        return_maturity_at: str,
        final_sale_price_minor: int,
        reconciled_fees_minor: int,
        reconciled_fulfillment_cost_minor: int,
        mature_margin_minor: int,
    ) -> dict[str, Any]:
        namespace = _namespace(offer.merchant_id, offer.store_id)
        mature = _event(
            "mature",
            event_id=f"{transaction_id}_mature",
            namespace=namespace,
            transaction_id=transaction_id,
            occurred_at=occurred_at,
            source="marginpilot_return_window",
            mature_outcome={
                "payment_resolution": "paid",
                "refund_return_maturity_date": return_maturity_at,
                "reconciled_fees": money(reconciled_fees_minor, offer.currency),
                "reconciled_fulfillment_costs": money(reconciled_fulfillment_cost_minor, offer.currency),
                "mature_contribution_margin": money(mature_margin_minor, offer.currency),
            },
        )
        self.state.append_event(mature)
        consent_grant(
            data_dir=self.data_dir,
            merchant_id=offer.merchant_id,
            store_id=offer.store_id,
            granted_at="2026-06-22T09:50:00+00:00",
        )
        operational = self.operational.get(merchant_id=offer.merchant_id, store_id=offer.store_id, operational_transaction_id=transaction_id)
        if operational is None:
            raise ShopifyWebhookError("missing operational transaction for research projection")
        mapping = EphemeralMappingLayer(secret=stable_hash({"store": offer.store_id, "purpose": "shopify_e2e"}).encode("utf-8"))
        evidence = self.consent.latest_evidence(
            merchant_id=offer.merchant_id,
            store_id=offer.store_id,
            purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
            as_of=occurred_at,
        )
        research = ResearchEventRecord.from_operational(
            operational,
            mapping,
            event_id=f"research_{transaction_id}",
            occurred_at=occurred_at,
            offer_context={
                "surface": "product_page_offer",
                "category": "refurbished technology",
                "asking_price_minor": offer.asking_price_minor,
                "buyer_offer_amount_minor": offer.buyer_offer_amount_minor,
                "counter_amount_minor": final_sale_price_minor,
            },
            decisions={"selected_action": "counter_at_amount", "amount_minor": final_sale_price_minor},
            outcomes={"buyer_paid": True, "return_window_matured": True, "returned": False},
            financial_components={
                "cost_basis_minor": offer.cost_basis_minor,
                "reconciled_fees_minor": reconciled_fees_minor,
                "reconciled_fulfillment_costs_minor": reconciled_fulfillment_cost_minor,
                "mature_contribution_margin_minor": mature_margin_minor,
            },
            consent_policy_version=evidence["consent_policy_version"],
            consent_policy_hash=evidence["policy_hash"],
            provenance={"source": "shopify_development_adapter"},
        )
        record = self.research.append(research)
        export = research_export(
            data_dir=self.data_dir,
            merchant_id=offer.merchant_id,
            store_id=offer.store_id,
            purpose=MERCHANT_SPECIFIC_MODEL_TRAINING,
            as_of=occurred_at,
        )
        return {
            "state": self.state.inspect(namespace, transaction_id),
            "research_record_id": record["record_id"],
            "research_export": export,
        }


def _validate_offer_input(offer: ShopifyOfferInput) -> None:
    if offer.quantity <= 0:
        raise ValueError("quantity must be positive")
    for field in ["asking_price_minor", "buyer_offer_amount_minor", "cost_basis_minor", "shipping_cost_minor", "fulfillment_cost_minor"]:
        if getattr(offer, field) < 0:
            raise ValueError(f"{field} may not be negative")
    if offer.contact_email and "@" not in offer.contact_email:
        raise ValueError("contact_email must be an email-like operational value")


def _namespace(merchant_id: str, store_id: str) -> str:
    return f"{merchant_id}:{store_id}"


def _event(transition_to: str, *, event_id: str, namespace: str, transaction_id: str, occurred_at: str, source: str, currency: str = "USD", received_at: str | None = None, idempotency_key: str | None = None, **extra: Any) -> dict[str, Any]:
    body = {
        "schema_version": MARGINPILOT_STATE_SCHEMA_VERSION,
        "event_id": event_id,
        "merchant_namespace": namespace,
        "transaction_id": transaction_id,
        "occurred_at": occurred_at,
        "received_at": received_at or occurred_at,
        "source": source,
        "idempotency_key": idempotency_key or event_id,
        "transition_to": transition_to,
        "currency": currency,
    }
    body.update(extra)
    return body


def _topic_to_transition(topic: str, payload: dict[str, Any]) -> str:
    normalized = topic.lower().replace(".", "/")
    if normalized in {"orders/create", "orders/created"}:
        return "order_created"
    if normalized in {"orders/paid", "orders/updated"} and payload.get("financial_status") in {"paid", "partially_paid"}:
        return "paid" if payload.get("financial_status") == "paid" else "payment_pending"
    if normalized in {"refunds/create", "refunds/created"}:
        return "fully_refunded" if payload.get("refund_status") == "full" else "partially_refunded"
    if normalized in {"returns/open", "returns/opened"}:
        return "return_opened"
    if normalized in {"returns/close", "returns/closed"}:
        return "return_closed"
    if normalized in {"orders/cancelled", "orders/cancel"}:
        return "cancelled"
    raise ShopifyWebhookError(f"unsupported Shopify webhook topic: {topic}")


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None
