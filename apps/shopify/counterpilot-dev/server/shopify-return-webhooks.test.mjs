import assert from "node:assert/strict";
import crypto from "node:crypto";
import test from "node:test";

import {
  ShopifyWebhookError,
  verifyShopifyWebhookHmac,
} from "./shopify-order-webhooks.mjs";
import { normalizeShopifyReturnWebhook } from "./shopify-return-webhooks.mjs";

const SECRET = "shopify-webhook-secret";

function sign(rawBody, secret = SECRET) {
  return crypto.createHmac("sha256", secret).update(rawBody).digest("base64");
}

function headers(rawBody, overrides = {}) {
  return {
    "x-shopify-topic": "returns/request",
    "x-shopify-webhook-id": "delivery_return_123",
    "x-shopify-shop-domain": "counterpilot-dev.myshopify.com",
    "x-shopify-hmac-sha256": sign(rawBody),
    ...overrides,
  };
}

function payload(overrides = {}) {
  return {
    id: 246,
    admin_graphql_api_id: "gid://shopify/Return/246",
    status: "requested",
    order: {
      id: 123,
      admin_graphql_api_id: "gid://shopify/Order/123",
    },
    total_return_line_items: 2,
    created_at: "2026-06-23T14:10:00-04:00",
    updated_at: "2026-06-23T14:11:00-04:00",
    ...overrides,
  };
}

test("normalizeShopifyReturnWebhook records open exposure from request topic", () => {
  const rawBody = Buffer.from(JSON.stringify(payload()));
  const returned = normalizeShopifyReturnWebhook({
    rawBody,
    headers: headers(rawBody),
    webhookSecret: SECRET,
  });

  assert.equal(returned.topic, "returns/request");
  assert.equal(returned.source, "shopify_returns_request_webhook");
  assert.equal(returned.order.reference, "gid://shopify/Order/123");
  assert.equal(returned.return.reference, "gid://shopify/Return/246");
  assert.equal(returned.return.status, "requested");
  assert.equal(returned.return.exposureState, "open");
  assert.equal(returned.return.totalReturnLineItems, 2);
  assert.equal(returned.productionEvidence, false);
});

test("normalizeShopifyReturnWebhook maps order_id payloads and closed topics", () => {
  const rawBody = Buffer.from(
    JSON.stringify(
      payload({
        order: undefined,
        order_id: 456,
        status: "closed",
      }),
    ),
  );
  const returned = normalizeShopifyReturnWebhook({
    rawBody,
    headers: headers(rawBody, { "x-shopify-topic": "returns/close" }),
    webhookSecret: SECRET,
  });

  assert.equal(returned.source, "shopify_returns_close_webhook");
  assert.equal(returned.order.reference, "gid://shopify/Order/456");
  assert.equal(returned.return.status, "closed");
  assert.equal(returned.return.exposureState, "closed");
});

test("normalizeShopifyReturnWebhook verifies raw body HMAC before parsing", () => {
  const rawBody = Buffer.from(JSON.stringify(payload()));
  assert.equal(
    verifyShopifyWebhookHmac(rawBody, headers(rawBody), SECRET),
    sign(rawBody),
  );
  assert.throws(
    () =>
      normalizeShopifyReturnWebhook({
        rawBody: Buffer.from("{not json"),
        headers: headers(rawBody),
        webhookSecret: SECRET,
      }),
    ShopifyWebhookError,
  );
});

test("normalizeShopifyReturnWebhook rejects unsupported return topics", () => {
  const rawBody = Buffer.from(JSON.stringify(payload()));
  assert.throws(
    () =>
      normalizeShopifyReturnWebhook({
        rawBody,
        headers: headers(rawBody, {
          "x-shopify-topic": "returns/update",
        }),
        webhookSecret: SECRET,
      }),
    ShopifyWebhookError,
  );
});
