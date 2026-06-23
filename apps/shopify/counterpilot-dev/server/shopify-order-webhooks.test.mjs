import assert from "node:assert/strict";
import crypto from "node:crypto";
import test from "node:test";

import {
  ShopifyWebhookError,
  normalizeShopifyOrderWebhook,
  verifyShopifyWebhookHmac,
} from "./shopify-order-webhooks.mjs";

const SECRET = "shopify-webhook-secret";

function sign(rawBody, secret = SECRET) {
  return crypto.createHmac("sha256", secret).update(rawBody).digest("base64");
}

function headers(rawBody, overrides = {}) {
  return {
    "x-shopify-topic": "orders/paid",
    "x-shopify-webhook-id": "delivery_123",
    "x-shopify-shop-domain": "counterpilot-dev.myshopify.com",
    "x-shopify-hmac-sha256": sign(rawBody),
    ...overrides,
  };
}

function payload(overrides = {}) {
  return {
    id: 123,
    admin_graphql_api_id: "gid://shopify/Order/123",
    name: "#1001",
    processed_at: "2026-06-23T14:00:00-04:00",
    currency: "USD",
    financial_status: "paid",
    current_total_price: "610.00",
    current_total_discounts: "0.00",
    current_total_tax: "0.00",
    current_shipping_price_set: {
      shop_money: { amount: "0.00", currency_code: "USD" },
    },
    note_attributes: [
      { name: "counterpilot_transaction_id", value: "cp_offer_123" },
    ],
    ...overrides,
  };
}

test("verifyShopifyWebhookHmac validates against the raw body", () => {
  const rawBody = Buffer.from(JSON.stringify(payload()));
  assert.equal(
    verifyShopifyWebhookHmac(rawBody, headers(rawBody), SECRET),
    sign(rawBody),
  );
  assert.throws(
    () =>
      verifyShopifyWebhookHmac(
        Buffer.from(JSON.stringify({ different: true })),
        headers(rawBody),
        SECRET,
      ),
    ShopifyWebhookError,
  );
});

test("normalizeShopifyOrderWebhook extracts minimal paid order evidence", () => {
  const rawBody = Buffer.from(JSON.stringify(payload()));
  const webhook = normalizeShopifyOrderWebhook({
    rawBody,
    headers: headers(rawBody),
    webhookSecret: SECRET,
  });

  assert.equal(webhook.topic, "orders/paid");
  assert.equal(webhook.transactionId, "cp_offer_123");
  assert.equal(webhook.paidObserved, true);
  assert.equal(webhook.productionEvidence, false);
  assert.equal(webhook.order.reference, "gid://shopify/Order/123");
  assert.equal(webhook.order.orderTotalMinor, 61000);
});

test("normalizeShopifyOrderWebhook can extract the transaction from tags", () => {
  const rawBody = Buffer.from(
    JSON.stringify(
      payload({
        note_attributes: [],
        tags: "counterpilot_transaction_id:cp_offer_from_tag",
      }),
    ),
  );
  const webhook = normalizeShopifyOrderWebhook({
    rawBody,
    headers: headers(rawBody),
    webhookSecret: SECRET,
  });
  assert.equal(webhook.transactionId, "cp_offer_from_tag");
});

test("normalizeShopifyOrderWebhook rejects malformed transaction attributes", () => {
  const rawBody = Buffer.from(
    JSON.stringify(
      payload({
        note_attributes: [
          { name: "counterpilot_transaction_id", value: "not safe" },
        ],
      }),
    ),
  );
  assert.throws(
    () =>
      normalizeShopifyOrderWebhook({
        rawBody,
        headers: headers(rawBody),
        webhookSecret: SECRET,
      }),
    ShopifyWebhookError,
  );
});
