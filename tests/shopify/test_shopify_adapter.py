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
        "X-Shopify-Webhook-Id": delivery_id,
        "X-Shopify-Topic": topic,
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
                {"occurred_at": "2026-06-22T10:12:00+00:00", "received_at": "2026-06-22T10:12:01+00:00"},
            )
            first = adapter.ingest_webhook(raw_body=raw, headers=headers, merchant_id=offer.merchant_id, store_id=offer.store_id, transaction_id=transaction_id)
            second = adapter.ingest_webhook(raw_body=raw, headers=headers, merchant_id=offer.merchant_id, store_id=offer.store_id, transaction_id=transaction_id)

            self.assertEqual(first["transition"], "order_created")
            self.assertTrue(second["result"]["idempotent_replay"])
            self.assertEqual(second["state"]["event_count"], 5)


if __name__ == "__main__":
    unittest.main()
