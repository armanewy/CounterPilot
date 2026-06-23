import assert from "node:assert/strict";
import crypto from "node:crypto";
import test from "node:test";

import {
  ShopifyWebhookError,
  verifyShopifyWebhookHmac,
} from "./shopify-order-webhooks.mjs";
import { normalizeShopifyRefundWebhook } from "./shopify-refund-webhooks.mjs";

const SECRET = "shopify-webhook-secret";

function sign(rawBody, secret = SECRET) {
  return crypto.createHmac("sha256", secret).update(rawBody).digest("base64");
}

function headers(rawBody, overrides = {}) {
  return {
    "x-shopify-topic": "refunds/create",
    "x-shopify-webhook-id": "delivery_refund_123",
    "x-shopify-shop-domain": "counterpilot-dev.myshopify.com",
    "x-shopify-hmac-sha256": sign(rawBody),
    ...overrides,
  };
}

function payload(overrides = {}) {
  return {
    id: 987,
    admin_graphql_api_id: "gid://shopify/Refund/987",
    order_id: 123,
    processed_at: "2026-06-23T14:05:00-04:00",
    refund_line_items: [
      {
        quantity: 1,
        subtotal_set: {
          shop_money: { amount: "12.00", currency_code: "USD" },
        },
        total_tax_set: {
          shop_money: { amount: "1.00", currency_code: "USD" },
        },
      },
    ],
    refund_shipping_lines: [],
    order_adjustments: [],
    transactions: [
      {
        id: 444,
        kind: "refund",
        status: "success",
        amount: "15.00",
        currency: "USD",
        admin_graphql_api_id: "gid://shopify/OrderTransaction/444",
      },
    ],
    ...overrides,
  };
}

test("normalizeShopifyRefundWebhook uses successful refund transactions first", () => {
  const rawBody = Buffer.from(JSON.stringify(payload()));
  const refund = normalizeShopifyRefundWebhook({
    rawBody,
    headers: headers(rawBody),
    webhookSecret: SECRET,
  });

  assert.equal(refund.topic, "refunds/create");
  assert.equal(refund.order.reference, "gid://shopify/Order/123");
  assert.equal(refund.refund.reference, "gid://shopify/Refund/987");
  assert.equal(refund.refund.refundTotalMinor, 1500);
  assert.equal(refund.refund.currency, "USD");
  assert.equal(refund.refund.amountSource, "successful_refund_transactions");
  assert.equal(refund.needsReconciliation, false);
});

test("normalizeShopifyRefundWebhook falls back to line item evidence", () => {
  const rawBody = Buffer.from(
    JSON.stringify(
      payload({
        transactions: [{ kind: "refund", status: "failure" }],
        refund_shipping_lines: [
          {
            subtotal_set: {
              shop_money: { amount: "2.50", currency_code: "USD" },
            },
          },
        ],
      }),
    ),
  );
  const refund = normalizeShopifyRefundWebhook({
    rawBody,
    headers: headers(rawBody),
    webhookSecret: SECRET,
  });

  assert.equal(refund.refund.refundTotalMinor, 1550);
  assert.equal(refund.refund.amountSource, "line_item_fallback");
});

test("normalizeShopifyRefundWebhook flags missing currency for reconciliation", () => {
  const rawBody = Buffer.from(
    JSON.stringify(
      payload({
        transactions: [
          { id: 444, kind: "refund", status: "success", amount: "15.00" },
        ],
      }),
    ),
  );
  const refund = normalizeShopifyRefundWebhook({
    rawBody,
    headers: headers(rawBody),
    webhookSecret: SECRET,
  });

  assert.equal(refund.needsReconciliation, true);
  assert.equal(
    refund.reconciliationReason,
    "missing_refund_transaction_currency",
  );
});

test("normalizeShopifyRefundWebhook rejects mixed valid and missing transaction currency", () => {
  const rawBody = Buffer.from(
    JSON.stringify(
      payload({
        transactions: [
          {
            id: 444,
            kind: "refund",
            status: "success",
            amount: "10.00",
            currency: "USD",
          },
          {
            id: 445,
            kind: "refund",
            status: "success",
            amount: "5.00",
          },
        ],
      }),
    ),
  );
  const refund = normalizeShopifyRefundWebhook({
    rawBody,
    headers: headers(rawBody),
    webhookSecret: SECRET,
  });

  assert.equal(refund.needsReconciliation, true);
  assert.equal(
    refund.reconciliationReason,
    "missing_refund_transaction_currency",
  );
});

test("normalizeShopifyRefundWebhook rejects fallback amounts with missing currency", () => {
  const rawBody = Buffer.from(
    JSON.stringify(
      payload({
        transactions: [{ kind: "refund", status: "failure" }],
        refund_line_items: [
          {
            quantity: 1,
            subtotal_set: {
              shop_money: { amount: "12.00", currency_code: "USD" },
            },
            total_tax_set: {
              shop_money: { amount: "1.00" },
            },
          },
        ],
      }),
    ),
  );
  const refund = normalizeShopifyRefundWebhook({
    rawBody,
    headers: headers(rawBody),
    webhookSecret: SECRET,
  });

  assert.equal(refund.needsReconciliation, true);
  assert.equal(refund.reconciliationReason, "missing_refund_currency");
});

test("normalizeShopifyRefundWebhook verifies raw body HMAC before parsing", () => {
  const rawBody = Buffer.from(JSON.stringify(payload()));
  assert.equal(
    verifyShopifyWebhookHmac(rawBody, headers(rawBody), SECRET),
    sign(rawBody),
  );
  assert.throws(
    () =>
      normalizeShopifyRefundWebhook({
        rawBody: Buffer.from("{not json"),
        headers: headers(rawBody),
        webhookSecret: SECRET,
      }),
    ShopifyWebhookError,
  );
});
