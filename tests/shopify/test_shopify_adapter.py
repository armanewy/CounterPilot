from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))

import _bootstrap  # noqa: F401,E402

from integrations.shopify import DeterministicFakeShopifyProvider, ShopifyDevelopmentAdapter, ShopifyOfferInput, sign_webhook
from integrations.shopify.token_store import REQUIRED_DEVELOPMENT_SCOPES, SHOPIFY_TOKEN_COLLECTION
from integrations.shopify.webhooks import ShopifyWebhookError, verify_webhook_hmac


SECRET = b"shopify-webhook-secret"


def _offer() -> ShopifyOfferInput:
    return ShopifyOfferInput(
        merchant_id="merchant_demo_refurb",
        store_id="store_demo_shopify",
        store_domain="marginpilot-dev-store.myshopify.com",
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


def _webhook(topic: str, delivery_id: str, payload: dict) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return raw, {
        "X-Shopify-Hmac-Sha256": sign_webhook(raw, SECRET),
        "X-Shopify-Shop-Domain": "marginpilot-dev-store.myshopify.com",
        "X-Shopify-Webhook-Id": delivery_id,
        "X-Shopify-Topic": topic,
    }


def _resource_ids(provider: DeterministicFakeShopifyProvider) -> dict[str, str]:
    result = provider.created_draft_orders[0]["result"]
    return {
        "checkout_gid": result["resource_ids"]["checkout_gid"],
        "draft_order_gid": result["draft_order_id"],
        "order_gid": "gid://shopify/Order/3001",
    }


class ShopifyAdapterTests(unittest.TestCase):
    def test_webhook_hmac_and_delivery_id_are_verified(self) -> None:
        raw, headers = _webhook("orders/create", "delivery_001", {"occurred_at": "2026-06-22T10:00:00+00:00"})
        self.assertEqual(verify_webhook_hmac(raw, headers, SECRET), "delivery_001")
        bad_headers = dict(headers)
        bad_headers["X-Shopify-Hmac-Sha256"] = "bad"
        with self.assertRaises(ShopifyWebhookError):
            verify_webhook_hmac(raw, bad_headers, SECRET)

    def test_fake_provider_creates_redacted_draft_order_reference(self) -> None:
        provider = DeterministicFakeShopifyProvider()
        adapter = ShopifyDevelopmentAdapter(data_dir=tempfile.mkdtemp(), provider=provider, webhook_secret=SECRET)
        offer = _offer()
        submitted = adapter.submit_offer(offer, occurred_at="2026-06-22T10:00:00+00:00")
        adapter.merchant_counter(
            merchant_id=offer.merchant_id,
            store_id=offer.store_id,
            transaction_id=submitted["transaction_id"],
            amount_minor=76000,
            shipping_discount_minor=3400,
            occurred_at="2026-06-22T10:05:00+00:00",
        )
        adapter.buyer_accept(
            merchant_id=offer.merchant_id,
            store_id=offer.store_id,
            transaction_id=submitted["transaction_id"],
            occurred_at="2026-06-22T10:10:00+00:00",
        )
        checkout = adapter.create_checkout(
            offer=offer,
            transaction_id=submitted["transaction_id"],
            counter_amount_minor=76000,
            occurred_at="2026-06-22T10:11:00+00:00",
            reserve_inventory=True,
        )

        rendered = json.dumps(checkout, sort_keys=True)
        self.assertIn("draft_order", checkout)
        self.assertNotIn("buyer@example.com", rendered)
        self.assertNotIn("gid://shopify", rendered)
        delivery = adapter.checkout_delivery(merchant_id=offer.merchant_id, store_id=offer.store_id, transaction_id=submitted["transaction_id"])
        self.assertIn("https://", delivery["checkout_url"])
        self.assertEqual(provider.created_draft_orders[0]["request"]["discount_minor"], 14000)

    def test_checkout_creation_validates_state_before_provider_call_and_reuses_existing_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = DeterministicFakeShopifyProvider()
            adapter = ShopifyDevelopmentAdapter(data_dir=tmp, provider=provider, webhook_secret=SECRET)
            offer = _offer()
            submitted = adapter.submit_offer(offer, occurred_at="2026-06-22T10:00:00+00:00")
            with self.assertRaises(ShopifyWebhookError):
                adapter.create_checkout(
                    offer=offer,
                    transaction_id=submitted["transaction_id"],
                    counter_amount_minor=76000,
                    occurred_at="2026-06-22T10:01:00+00:00",
                )
            self.assertEqual(provider.created_draft_orders, [])

            adapter.merchant_counter(
                merchant_id=offer.merchant_id,
                store_id=offer.store_id,
                transaction_id=submitted["transaction_id"],
                amount_minor=76000,
                shipping_discount_minor=3400,
                occurred_at="2026-06-22T10:05:00+00:00",
            )
            adapter.buyer_accept(
                merchant_id=offer.merchant_id,
                store_id=offer.store_id,
                transaction_id=submitted["transaction_id"],
                occurred_at="2026-06-22T10:10:00+00:00",
            )
            adapter.create_checkout(
                offer=offer,
                transaction_id=submitted["transaction_id"],
                counter_amount_minor=76000,
                occurred_at="2026-06-22T10:11:00+00:00",
            )
            adapter.create_checkout(
                offer=offer,
                transaction_id=submitted["transaction_id"],
                counter_amount_minor=76000,
                occurred_at="2026-06-22T10:12:00+00:00",
            )
            self.assertEqual(len(provider.created_draft_orders), 1)

    def test_token_storage_is_encrypted_and_app_uninstall_revokes_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ShopifyDevelopmentAdapter(data_dir=tmp, provider=DeterministicFakeShopifyProvider(), webhook_secret=SECRET)
            token = "shpat_development_secret_token"
            installed = adapter.install_app_token(
                merchant_id="merchant_demo_refurb",
                store_id="store_demo_shopify",
                store_domain="marginpilot-dev-store.myshopify.com",
                access_token=token,
                scopes=REQUIRED_DEVELOPMENT_SCOPES,
                installed_at="2026-06-22T09:00:00+00:00",
            )

            rendered_install = json.dumps(installed, sort_keys=True)
            self.assertNotIn(token, rendered_install)
            raw = adapter.tokens.adapter.raw_ciphertext(SHOPIFY_TOKEN_COLLECTION, installed["record_id"])
            self.assertNotIn(token.encode("utf-8"), raw)

            raw_body, headers = _webhook("app/uninstalled", "delivery_uninstall", {"occurred_at": "2026-06-22T11:00:00+00:00"})
            result = adapter.ingest_app_webhook(raw_body=raw_body, headers=headers, merchant_id="merchant_demo_refurb", store_id="store_demo_shopify")
            self.assertEqual(result["action"], "token_revoked")
            self.assertEqual(
                adapter.tokens.get(merchant_id="merchant_demo_refurb", store_id="store_demo_shopify").revoked_at,
                "2026-06-22T11:00:00+00:00",
            )
            bad_headers = dict(headers)
            bad_headers["X-Shopify-Shop-Domain"] = "other-dev-store.myshopify.com"
            with self.assertRaises(ShopifyWebhookError):
                adapter.ingest_app_webhook(raw_body=raw_body, headers=bad_headers, merchant_id="merchant_demo_refurb", store_id="store_demo_shopify")

    def test_offer_inbox_is_namespaced_by_merchant_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ShopifyDevelopmentAdapter(data_dir=tmp, provider=DeterministicFakeShopifyProvider(), webhook_secret=SECRET)
            offer = _offer()
            submitted = adapter.submit_offer(offer, occurred_at="2026-06-22T10:00:00+00:00")
            own_inbox = adapter.offer_inbox(merchant_id=offer.merchant_id, store_id=offer.store_id)
            other_inbox = adapter.offer_inbox(merchant_id=offer.merchant_id, store_id="other_store")

            self.assertEqual(own_inbox["offers"][0]["transaction_id"], submitted["transaction_id"])
            self.assertEqual(other_inbox["offers"], [])

    def test_compliance_topics_are_verified_without_transaction_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ShopifyDevelopmentAdapter(data_dir=tmp, provider=DeterministicFakeShopifyProvider(), webhook_secret=SECRET)
            adapter.install_app_token(
                merchant_id="merchant_demo_refurb",
                store_id="store_demo_shopify",
                store_domain="marginpilot-dev-store.myshopify.com",
                access_token="shpat_development_secret_token",
                scopes=REQUIRED_DEVELOPMENT_SCOPES,
                installed_at="2026-06-22T09:00:00+00:00",
            )
            raw_body, headers = _webhook("customers/redact", "delivery_customer_redact", {"occurred_at": "2026-06-22T11:05:00+00:00"})
            result = adapter.ingest_app_webhook(raw_body=raw_body, headers=headers, merchant_id="merchant_demo_refurb", store_id="store_demo_shopify")
            self.assertEqual(result["action"], "compliance_topic_received")

    def test_admin_accept_decline_and_expiration_are_state_machine_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ShopifyDevelopmentAdapter(data_dir=tmp, provider=DeterministicFakeShopifyProvider(), webhook_secret=SECRET)
            accepted_offer = _offer()
            accepted = adapter.submit_offer(accepted_offer, occurred_at="2026-06-22T10:00:00+00:00")
            accepted_state = adapter.merchant_accept(
                merchant_id=accepted_offer.merchant_id,
                store_id=accepted_offer.store_id,
                transaction_id=accepted["transaction_id"],
                occurred_at="2026-06-22T10:02:00+00:00",
                merchant_floor_minor=69000,
            )
            self.assertEqual(accepted_state["current_state"], "merchant_accepted")

            declined_offer = _offer()
            declined_offer = ShopifyOfferInput(**{**declined_offer.__dict__, "buyer_session_reference": "session-ref-decline"})
            declined = adapter.submit_offer(declined_offer, occurred_at="2026-06-22T10:03:00+00:00")
            declined_state = adapter.merchant_decline(
                merchant_id=declined_offer.merchant_id,
                store_id=declined_offer.store_id,
                transaction_id=declined["transaction_id"],
                occurred_at="2026-06-22T10:04:00+00:00",
            )
            self.assertEqual(declined_state["current_state"], "merchant_declined")

            expired_offer = _offer()
            expired_offer = ShopifyOfferInput(**{**expired_offer.__dict__, "buyer_session_reference": "session-ref-expire"})
            expired = adapter.submit_offer(expired_offer, occurred_at="2026-06-22T10:05:00+00:00")
            expired_state = adapter.expire_offer(
                merchant_id=expired_offer.merchant_id,
                store_id=expired_offer.store_id,
                transaction_id=expired["transaction_id"],
                occurred_at="2026-06-22T10:06:00+00:00",
            )
            self.assertEqual(expired_state["current_state"], "offer_expired")

    def test_webhooks_are_idempotent_and_project_to_existing_state_machine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = DeterministicFakeShopifyProvider()
            adapter = ShopifyDevelopmentAdapter(data_dir=tmp, provider=provider, webhook_secret=SECRET)
            offer = _offer()
            submitted = adapter.submit_offer(offer, occurred_at="2026-06-22T10:00:00+00:00")
            transaction_id = submitted["transaction_id"]
            adapter.merchant_counter(
                merchant_id=offer.merchant_id,
                store_id=offer.store_id,
                transaction_id=transaction_id,
                amount_minor=76000,
                shipping_discount_minor=3400,
                occurred_at="2026-06-22T10:05:00+00:00",
            )
            adapter.buyer_accept(
                merchant_id=offer.merchant_id,
                store_id=offer.store_id,
                transaction_id=transaction_id,
                occurred_at="2026-06-22T10:10:00+00:00",
            )
            adapter.create_checkout(offer=offer, transaction_id=transaction_id, counter_amount_minor=76000, occurred_at="2026-06-22T10:11:00+00:00")

            raw, headers = _webhook(
                "orders/create",
                "delivery_order",
                {
                    "occurred_at": "2026-06-22T10:12:00+00:00",
                    "received_at": "2026-06-22T10:12:01+00:00",
                    "resource_ids": _resource_ids(provider),
                },
            )
            first = adapter.ingest_webhook(raw_body=raw, headers=headers, merchant_id=offer.merchant_id, store_id=offer.store_id, transaction_id=transaction_id)
            second = adapter.ingest_webhook(raw_body=raw, headers=headers, merchant_id=offer.merchant_id, store_id=offer.store_id, transaction_id=transaction_id)

            self.assertEqual(first["transition"], "order_created")
            self.assertFalse(first["delivery_replay"])
            self.assertTrue(second["delivery_replay"])
            self.assertTrue(second["result"]["idempotent_replay"])
            self.assertEqual(second["state"]["event_count"], 5)

    def test_webhook_delivery_id_cannot_be_rebound_to_another_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = DeterministicFakeShopifyProvider()
            adapter = ShopifyDevelopmentAdapter(data_dir=tmp, provider=provider, webhook_secret=SECRET)
            offer_one = _offer()
            one = adapter.submit_offer(offer_one, occurred_at="2026-06-22T10:00:00+00:00")
            adapter.merchant_counter(
                merchant_id=offer_one.merchant_id,
                store_id=offer_one.store_id,
                transaction_id=one["transaction_id"],
                amount_minor=76000,
                shipping_discount_minor=3400,
                occurred_at="2026-06-22T10:05:00+00:00",
            )
            adapter.buyer_accept(merchant_id=offer_one.merchant_id, store_id=offer_one.store_id, transaction_id=one["transaction_id"], occurred_at="2026-06-22T10:10:00+00:00")
            adapter.create_checkout(offer=offer_one, transaction_id=one["transaction_id"], counter_amount_minor=76000, occurred_at="2026-06-22T10:11:00+00:00")
            raw, headers = _webhook(
                "orders/create",
                "delivery_reused",
                {"occurred_at": "2026-06-22T10:12:00+00:00", "resource_ids": _resource_ids(provider)},
            )
            adapter.ingest_webhook(raw_body=raw, headers=headers, merchant_id=offer_one.merchant_id, store_id=offer_one.store_id, transaction_id=one["transaction_id"])

            offer_two = ShopifyOfferInput(**{**_offer().__dict__, "buyer_session_reference": "session-ref-two"})
            two = adapter.submit_offer(offer_two, occurred_at="2026-06-22T10:20:00+00:00")
            adapter.merchant_counter(
                merchant_id=offer_two.merchant_id,
                store_id=offer_two.store_id,
                transaction_id=two["transaction_id"],
                amount_minor=76000,
                shipping_discount_minor=3400,
                occurred_at="2026-06-22T10:21:00+00:00",
            )
            adapter.buyer_accept(merchant_id=offer_two.merchant_id, store_id=offer_two.store_id, transaction_id=two["transaction_id"], occurred_at="2026-06-22T10:22:00+00:00")
            adapter.create_checkout(offer=offer_two, transaction_id=two["transaction_id"], counter_amount_minor=76000, occurred_at="2026-06-22T10:23:00+00:00")
            with self.assertRaises(ShopifyWebhookError):
                adapter.ingest_webhook(raw_body=raw, headers=headers, merchant_id=offer_two.merchant_id, store_id=offer_two.store_id, transaction_id=two["transaction_id"])

    def test_webhook_economics_rejects_operational_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = DeterministicFakeShopifyProvider()
            adapter = ShopifyDevelopmentAdapter(data_dir=tmp, provider=provider, webhook_secret=SECRET)
            offer = _offer()
            submitted = adapter.submit_offer(offer, occurred_at="2026-06-22T10:00:00+00:00")
            adapter.merchant_counter(
                merchant_id=offer.merchant_id,
                store_id=offer.store_id,
                transaction_id=submitted["transaction_id"],
                amount_minor=76000,
                shipping_discount_minor=3400,
                occurred_at="2026-06-22T10:05:00+00:00",
            )
            adapter.buyer_accept(merchant_id=offer.merchant_id, store_id=offer.store_id, transaction_id=submitted["transaction_id"], occurred_at="2026-06-22T10:10:00+00:00")
            adapter.create_checkout(offer=offer, transaction_id=submitted["transaction_id"], counter_amount_minor=76000, occurred_at="2026-06-22T10:11:00+00:00")
            raw, headers = _webhook(
                "refunds/create",
                "delivery_bad_refund",
                {
                    "occurred_at": "2026-06-22T10:16:00+00:00",
                    "refund_status": "partial",
                    "resource_ids": _resource_ids(provider),
                    "economics": {"order_gid": "gid://shopify/Order/999"},
                },
            )
            with self.assertRaises(ShopifyWebhookError):
                adapter.ingest_webhook(raw_body=raw, headers=headers, merchant_id=offer.merchant_id, store_id=offer.store_id, transaction_id=submitted["transaction_id"])

    def test_webhook_economics_rejects_camel_case_identifier_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = DeterministicFakeShopifyProvider()
            adapter = ShopifyDevelopmentAdapter(data_dir=tmp, provider=provider, webhook_secret=SECRET)
            offer = _offer()
            submitted = adapter.submit_offer(offer, occurred_at="2026-06-22T10:00:00+00:00")
            adapter.merchant_counter(
                merchant_id=offer.merchant_id,
                store_id=offer.store_id,
                transaction_id=submitted["transaction_id"],
                amount_minor=76000,
                shipping_discount_minor=3400,
                occurred_at="2026-06-22T10:05:00+00:00",
            )
            adapter.buyer_accept(merchant_id=offer.merchant_id, store_id=offer.store_id, transaction_id=submitted["transaction_id"], occurred_at="2026-06-22T10:10:00+00:00")
            adapter.create_checkout(offer=offer, transaction_id=submitted["transaction_id"], counter_amount_minor=76000, occurred_at="2026-06-22T10:11:00+00:00")
            raw, headers = _webhook(
                "refunds/create",
                "delivery_camel_refund",
                {
                    "occurred_at": "2026-06-22T10:16:00+00:00",
                    "refund_status": "partial",
                    "resource_ids": _resource_ids(provider),
                    "economics": {"refundId": 5001},
                },
            )
            with self.assertRaises(ShopifyWebhookError):
                adapter.ingest_webhook(raw_body=raw, headers=headers, merchant_id=offer.merchant_id, store_id=offer.store_id, transaction_id=submitted["transaction_id"])

    def test_visibility_redacts_common_resource_identifier_shapes(self) -> None:
        adapter = ShopifyDevelopmentAdapter(data_dir=tempfile.mkdtemp(), provider=DeterministicFakeShopifyProvider(), webhook_secret=SECRET)
        surface = adapter.enable_offer_surface(
            merchant_id="merchant_demo_refurb",
            store_id="store_demo_shopify",
            visibility={"enabled": True, "productId": 123, "variantID": "456", "product_gid": "gid://shopify/Product/100"},
        )
        rendered = json.dumps(surface, sort_keys=True)
        self.assertNotIn("123", rendered)
        self.assertNotIn("456", rendered)
        self.assertNotIn("gid://shopify", rendered)

    def test_mature_margin_must_reconcile_to_observed_financial_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = DeterministicFakeShopifyProvider()
            adapter = ShopifyDevelopmentAdapter(data_dir=tmp, provider=provider, webhook_secret=SECRET)
            offer = _offer()
            submitted = adapter.submit_offer(offer, occurred_at="2026-06-22T10:00:00+00:00")
            transaction_id = submitted["transaction_id"]
            adapter.merchant_counter(merchant_id=offer.merchant_id, store_id=offer.store_id, transaction_id=transaction_id, amount_minor=76000, shipping_discount_minor=3400, occurred_at="2026-06-22T10:05:00+00:00")
            adapter.buyer_accept(merchant_id=offer.merchant_id, store_id=offer.store_id, transaction_id=transaction_id, occurred_at="2026-06-22T10:10:00+00:00")
            adapter.create_checkout(offer=offer, transaction_id=transaction_id, counter_amount_minor=76000, occurred_at="2026-06-22T10:11:00+00:00")
            raw, headers = _webhook("orders/create", "delivery_order_margin", {"occurred_at": "2026-06-22T10:12:00+00:00", "resource_ids": _resource_ids(provider)})
            adapter.ingest_webhook(raw_body=raw, headers=headers, merchant_id=offer.merchant_id, store_id=offer.store_id, transaction_id=transaction_id)
            raw, headers = _webhook(
                "orders/updated",
                "delivery_paid_margin",
                {
                    "occurred_at": "2026-06-22T10:14:00+00:00",
                    "financial_status": "paid",
                    "resource_ids": {"order_gid": "gid://shopify/Order/3001"},
                    "economics": {"final_sale_price": {"amount_minor": 76000, "currency": "USD"}},
                },
            )
            adapter.ingest_webhook(raw_body=raw, headers=headers, merchant_id=offer.merchant_id, store_id=offer.store_id, transaction_id=transaction_id)
            raw, headers = _webhook(
                "refunds/create",
                "delivery_refund_margin",
                {
                    "occurred_at": "2026-06-22T10:16:00+00:00",
                    "refund_status": "partial",
                    "resource_ids": {"order_gid": "gid://shopify/Order/3001", "refund_gid": "gid://shopify/Refund/5001"},
                    "economics": {"refund_amount": {"amount_minor": 1000, "currency": "USD"}},
                },
            )
            adapter.ingest_webhook(raw_body=raw, headers=headers, merchant_id=offer.merchant_id, store_id=offer.store_id, transaction_id=transaction_id)
            with self.assertRaises(ShopifyWebhookError):
                adapter.mature_and_export(
                    offer=offer,
                    transaction_id=transaction_id,
                    occurred_at="2026-07-22T10:20:00+00:00",
                    return_maturity_at="2026-07-22T10:20:00+00:00",
                    final_sale_price_minor=76000,
                    reconciled_fees_minor=2234,
                    reconciled_fulfillment_cost_minor=4600,
                    mature_margin_minor=999999,
                )
            with self.assertRaises(ShopifyWebhookError):
                adapter.mature_and_export(
                    offer=offer,
                    transaction_id=transaction_id,
                    occurred_at="2026-07-22T10:21:00+00:00",
                    return_maturity_at="2026-07-22T10:21:00+00:00",
                    final_sale_price_minor=86000,
                    reconciled_fees_minor=2234,
                    reconciled_fulfillment_cost_minor=4600,
                    mature_margin_minor=26166,
                )


if __name__ == "__main__":
    unittest.main()
