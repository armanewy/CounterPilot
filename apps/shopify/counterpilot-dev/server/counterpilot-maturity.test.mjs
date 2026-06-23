import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { runMaturityJob } from "./counterpilot-maturity.mjs";
import { createCounterpilotServer } from "./counterpilot-server.mjs";

const WEBHOOK_SECRET = "shopify-webhook-secret";

async function withServer(fn, options = {}) {
  const dataDir = await fs.mkdtemp(
    path.join(os.tmpdir(), "counterpilot-maturity-"),
  );
  const shopifyDraftOrderAdapter =
    options.shopifyDraftOrderAdapter ??
    (async () => ({
      draftOrderId: "gid://shopify/DraftOrder/checkout-created-1",
      checkoutUrl:
        "https://checkout.counterpilot.test/invoice/checkout-created-1",
    }));
  const server = createCounterpilotServer({
    dataDir,
    shopifyDraftOrderAdapter,
    ...options,
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const { port } = server.address();
  try {
    await fn({
      baseUrl: `http://127.0.0.1:${port}`,
      dataDir,
    });
  } finally {
    await new Promise((resolve) => server.close(resolve));
    await fs.rm(dataDir, { force: true, recursive: true });
  }
}

function validOffer(overrides = {}) {
  return {
    shop: "counterpilot-dev.myshopify.com",
    product_gid: "gid://shopify/Product/123",
    variant_gid: "gid://shopify/ProductVariant/456",
    product_title: "The Complete Snowboard",
    offer_amount: "610.00",
    currency: "USD",
    quantity: 1,
    buyer_email: "buyer@example.com",
    ...overrides,
  };
}

async function postJson(url, payload) {
  return fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function submitOffer(baseUrl, payload = validOffer()) {
  const response = await postJson(`${baseUrl}/counterpilot/offers`, payload);
  assert.equal(response.status, 201);
  const body = await response.json();
  return body.transaction_id;
}

async function merchantAccept(baseUrl, transactionId) {
  const response = await postJson(
    `${baseUrl}/counterpilot/merchant/offers/${encodeURIComponent(transactionId)}/accept`,
    { store_id: "counterpilot-dev.myshopify.com" },
  );
  assert.equal(response.status, 200);
  return response.json();
}

function buyerAcceptUrl(baseUrl, buyerResponsePath) {
  const url = new URL(
    buyerResponsePath.replace("/respond?", "/accept?"),
    baseUrl,
  );
  url.searchParams.set("shop", "counterpilot-dev.myshopify.com");
  return url.toString();
}

function signWebhookBody(rawBody, secret = WEBHOOK_SECRET) {
  return crypto.createHmac("sha256", secret).update(rawBody).digest("base64");
}

function orderWebhookPayload(transactionId, overrides = {}) {
  return {
    id: 123456789,
    admin_graphql_api_id: "gid://shopify/Order/123456789",
    name: "#1001",
    created_at: "2026-06-23T14:00:00-04:00",
    updated_at: "2026-06-23T14:01:00-04:00",
    processed_at: "2026-06-23T14:00:30-04:00",
    currency: "USD",
    presentment_currency: "USD",
    financial_status: "pending",
    current_total_price: "610.00",
    current_subtotal_price: "610.00",
    current_total_discounts: "0.00",
    current_total_tax: "0.00",
    current_shipping_price_set: {
      shop_money: { amount: "0.00", currency_code: "USD" },
    },
    note_attributes:
      transactionId === null
        ? []
        : [{ name: "counterpilot_transaction_id", value: transactionId }],
    tags: "counterpilot,counterpilot-negotiated",
    contact_email: "buyer@example.com",
    email: "buyer@example.com",
    phone: "555-0100",
    order_status_url:
      "https://counterpilot-dev.myshopify.com/orders/raw-status-token",
    shipping_address: {
      address1: "123 Union Ave",
      city: "Framingham",
    },
    customer: {
      email: "buyer@example.com",
    },
    ...overrides,
  };
}

function refundWebhookPayload(overrides = {}) {
  return {
    id: 987654321,
    admin_graphql_api_id: "gid://shopify/Refund/987654321",
    order_id: 123456789,
    created_at: "2026-06-23T14:05:00-04:00",
    processed_at: "2026-06-23T14:05:30-04:00",
    refund_line_items: [],
    refund_shipping_lines: [],
    order_adjustments: [],
    transactions: [
      {
        id: 444555666,
        kind: "refund",
        status: "success",
        amount: "15.00",
        currency: "USD",
        admin_graphql_api_id: "gid://shopify/OrderTransaction/444555666",
        gateway: "bogus",
        message: "refund processed for buyer@example.com",
      },
    ],
    note: "do not store this refund note",
    buyer_email: "buyer@example.com",
    phone: "555-0100",
    ...overrides,
  };
}

function returnWebhookPayload(overrides = {}) {
  return {
    id: 246813579,
    admin_graphql_api_id: "gid://shopify/Return/246813579",
    status: "requested",
    order: {
      id: 123456789,
      admin_graphql_api_id: "gid://shopify/Order/123456789",
    },
    total_return_line_items: 2,
    name: "#R1001",
    created_at: "2026-06-23T14:10:00-04:00",
    updated_at: "2026-06-23T14:11:00-04:00",
    decline: {
      reason: "return_period_ended",
      note: "Decline note with buyer@example.com and 555-0100",
    },
    reverse_deliveries: [
      {
        tracking: {
          tracking_url: "https://tracking.example/secret-return-tracking",
        },
      },
    ],
    buyer_email: "buyer@example.com",
    phone: "555-0100",
    ...overrides,
  };
}

async function postShopifyWebhook(
  baseUrl,
  { topic, deliveryId, payload, pathName = "orders" },
) {
  const rawBody = JSON.stringify(payload);
  return fetch(`${baseUrl}/counterpilot/webhooks/shopify/${pathName}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-shopify-topic": topic,
      "x-shopify-webhook-id": deliveryId,
      "x-shopify-shop-domain": "counterpilot-dev.myshopify.com",
      "x-shopify-hmac-sha256": signWebhookBody(rawBody),
    },
    body: rawBody,
  });
}

async function createCheckoutTransaction(baseUrl) {
  const transactionId = await submitOffer(baseUrl);
  const accepted = await merchantAccept(baseUrl, transactionId);
  const checkout = await fetch(
    buyerAcceptUrl(baseUrl, accepted.buyer_response_path),
    { method: "POST" },
  );
  assert.equal(checkout.status, 200);
  return transactionId;
}

async function createPaidTransaction(baseUrl) {
  const transactionId = await createCheckoutTransaction(baseUrl);
  const paid = await postShopifyWebhook(baseUrl, {
    topic: "orders/paid",
    deliveryId: `delivery_paid_${transactionId}`,
    payload: orderWebhookPayload(transactionId, {
      financial_status: "paid",
    }),
  });
  assert.equal(paid.status, 200);
  return transactionId;
}

async function createOrderButNotPaidTransaction(baseUrl) {
  const transactionId = await createCheckoutTransaction(baseUrl);
  const order = await postShopifyWebhook(baseUrl, {
    topic: "orders/create",
    deliveryId: `delivery_order_${transactionId}`,
    payload: orderWebhookPayload(transactionId),
  });
  assert.equal(order.status, 200);
  return transactionId;
}

async function postRefund(baseUrl, deliveryId, amount, overrides = {}) {
  const response = await postShopifyWebhook(baseUrl, {
    topic: "refunds/create",
    deliveryId,
    pathName: "refunds",
    payload: refundWebhookPayload({
      transactions: [
        {
          id: Number(deliveryId.replace(/\D/g, "").slice(-6) || 444555666),
          kind: "refund",
          status: "success",
          amount,
          currency: "USD",
          admin_graphql_api_id: `gid://shopify/OrderTransaction/${deliveryId}`,
        },
      ],
      ...overrides,
    }),
  });
  assert.equal(response.status, 200);
  return response;
}

async function postReturn(baseUrl, { topic, deliveryId, status }) {
  const response = await postShopifyWebhook(baseUrl, {
    topic,
    deliveryId,
    pathName: "returns",
    payload: returnWebhookPayload({ status }),
  });
  assert.equal(response.status, 200);
  return response;
}

async function writeMarginConfig(dataDir, overrides = {}) {
  const config = {
    schema_version: "counterpilot.margin_config.v1",
    maturity_window_days: 0,
    default_product_cost_minor: 42000,
    default_shipping_cost_minor: 3500,
    default_platform_fee_minor: 0,
    default_return_loss_minor: 0,
    currency: "USD",
    ...overrides,
  };
  await fs.writeFile(
    path.join(dataDir, "margin_config.json"),
    `${JSON.stringify(config, null, 2)}\n`,
    "utf8",
  );
  return config;
}

async function readOfferEvents(dataDir) {
  const persisted = await fs.readFile(
    path.join(dataDir, "offers.jsonl"),
    "utf8",
  );
  return persisted
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

async function runMaturity(dataDir, now = new Date("2026-06-23T19:00:00Z")) {
  return runMaturityJob({ dataDir, now });
}

function matureEvents(events) {
  return events.filter((event) => event.event_type === "mature");
}

function assertNoRawMaturityLeak(value) {
  const text = JSON.stringify(value);
  assert.doesNotMatch(text, /buyer@example\.com/);
  assert.doesNotMatch(text, /gid:\/\/shopify\//);
  assert.doesNotMatch(text, /checkout\.counterpilot\.test/);
  assert.doesNotMatch(text, /raw-status-token/);
  assert.doesNotMatch(text, /123 Union/);
  assert.doesNotMatch(text, /555-0100/);
  assert.doesNotMatch(text, /987654321/);
  assert.doesNotMatch(text, /246813579/);
  assert.doesNotMatch(text, /secret-return-tracking/);
  assert.doesNotMatch(text, /refund note/i);
  assert.doesNotMatch(text, /Decline note/i);
}

test("paid transaction with no refund and no return exposure appends mature", async () => {
  await withServer(
    async ({ baseUrl, dataDir }) => {
      await writeMarginConfig(dataDir);
      const transactionId = await createPaidTransaction(baseUrl);

      const result = await runMaturity(dataDir);
      assert.equal(result.appended, 1);
      const events = await readOfferEvents(dataDir);
      const mature = matureEvents(events).at(-1);

      assert.equal(mature.transaction_id, transactionId);
      assert.equal(mature.lifecycle_state, "mature");
      assert.equal(mature.paid_total_minor, 61000);
      assert.equal(mature.refund_total_minor, 0);
      assert.equal(mature.net_revenue_minor, 61000);
      assert.equal(mature.product_cost_minor, 42000);
      assert.equal(mature.shipping_cost_minor, 3500);
      assert.equal(mature.platform_fee_minor, 0);
      assert.equal(mature.return_loss_minor, 0);
      assert.equal(mature.mature_margin_minor, 15500);
      assert.equal(mature.currency, "USD");
      assert.equal(mature.return_exposure_state, "none");
      assert.equal(
        mature.margin_config_source,
        "counterpilot.margin_config.v1",
      );
      assert.match(mature.maturity_input_hash, /^sha256:[a-f0-9]{64}$/);
      assert.equal(mature.production_evidence, false);
      assertNoRawMaturityLeak(mature);
    },
    { shopifyWebhookSecret: WEBHOOK_SECRET },
  );
});

test("partial refund reduces net revenue and mature margin", async () => {
  await withServer(
    async ({ baseUrl, dataDir }) => {
      await writeMarginConfig(dataDir);
      await createPaidTransaction(baseUrl);
      await postRefund(baseUrl, "delivery_refund_partial_maturity", "15.00");

      await runMaturity(dataDir);
      const mature = matureEvents(await readOfferEvents(dataDir)).at(-1);
      assert.equal(mature.refund_total_minor, 1500);
      assert.equal(mature.net_revenue_minor, 59500);
      assert.equal(mature.mature_margin_minor, 14000);
      assertNoRawMaturityLeak(mature);
    },
    { shopifyWebhookSecret: WEBHOOK_SECRET },
  );
});

test("full refund can mature to zero net revenue", async () => {
  await withServer(
    async ({ baseUrl, dataDir }) => {
      await writeMarginConfig(dataDir);
      await createPaidTransaction(baseUrl);
      await postRefund(baseUrl, "delivery_refund_full_maturity", "610.00", {
        id: 987654322,
        admin_graphql_api_id: "gid://shopify/Refund/987654322",
      });

      await runMaturity(dataDir);
      const mature = matureEvents(await readOfferEvents(dataDir)).at(-1);
      assert.equal(mature.refund_total_minor, 61000);
      assert.equal(mature.net_revenue_minor, 0);
      assert.equal(mature.mature_margin_minor, -45500);
    },
    { shopifyWebhookSecret: WEBHOOK_SECRET },
  );
});

test("open return exposure blocks maturity and closed exposure allows it", async () => {
  await withServer(
    async ({ baseUrl, dataDir }) => {
      await writeMarginConfig(dataDir);
      await createPaidTransaction(baseUrl);
      await postReturn(baseUrl, {
        topic: "returns/request",
        deliveryId: "delivery_return_open_maturity",
        status: "requested",
      });

      const blocked = await runMaturity(dataDir);
      assert.equal(blocked.appended, 0);
      assert.equal(blocked.skipped.at(-1).reason, "return_exposure_open");

      await postReturn(baseUrl, {
        topic: "returns/close",
        deliveryId: "delivery_return_closed_maturity",
        status: "closed",
      });
      const allowed = await runMaturity(dataDir);
      assert.equal(allowed.appended, 1);
      const mature = matureEvents(await readOfferEvents(dataDir)).at(-1);
      assert.equal(mature.return_exposure_state, "closed");
      assert.equal(mature.mature_margin_minor, 15500);
    },
    { shopifyWebhookSecret: WEBHOOK_SECRET },
  );
});

test("reopened return exposure blocks maturity again after prior mature event", async () => {
  await withServer(
    async ({ baseUrl, dataDir }) => {
      await writeMarginConfig(dataDir);
      await createPaidTransaction(baseUrl);
      await postReturn(baseUrl, {
        topic: "returns/close",
        deliveryId: "delivery_return_closed_before_mature",
        status: "closed",
      });
      await runMaturity(dataDir);

      await postReturn(baseUrl, {
        topic: "returns/reopen",
        deliveryId: "delivery_return_reopened_after_mature",
        status: "open",
      });
      const blocked = await runMaturity(dataDir);
      assert.equal(blocked.appended, 0);
      assert.ok(
        blocked.skipped.some((item) => item.reason === "return_exposure_open"),
      );

      const inboxResponse = await fetch(
        `${baseUrl}/counterpilot/merchant/offers?shop=counterpilot-dev.myshopify.com`,
      );
      const inbox = await inboxResponse.json();
      assert.equal(inbox.offers[0].return_exposure_state, "open");
      assert.notEqual(inbox.offers[0].lifecycle_state, "mature");
      assert.equal("mature_margin_minor" in inbox.offers[0], false);
      assert.equal("maturity_input_hash" in inbox.offers[0], false);
    },
    { shopifyWebhookSecret: WEBHOOK_SECRET },
  );
});

test("missing margin config fails closed", async () => {
  await withServer(
    async ({ baseUrl, dataDir }) => {
      await createPaidTransaction(baseUrl);
      await assert.rejects(
        runMaturityJob({ dataDir }),
        /margin config is required/,
      );
    },
    { shopifyWebhookSecret: WEBHOOK_SECRET },
  );
});

test("currency mismatch skips maturity", async () => {
  await withServer(
    async ({ baseUrl, dataDir }) => {
      await writeMarginConfig(dataDir, { currency: "EUR" });
      await createPaidTransaction(baseUrl);

      const result = await runMaturity(dataDir);
      assert.equal(result.appended, 0);
      assert.ok(
        result.skipped.some((item) => item.reason === "currency_mismatch"),
      );
    },
    { shopifyWebhookSecret: WEBHOOK_SECRET },
  );
});

test("rerunning maturity job does not append duplicate mature events", async () => {
  await withServer(
    async ({ baseUrl, dataDir }) => {
      await writeMarginConfig(dataDir);
      await createPaidTransaction(baseUrl);

      const first = await runMaturity(dataDir);
      const second = await runMaturity(dataDir);
      assert.equal(first.appended, 1);
      assert.equal(second.appended, 0);
      assert.equal(
        second.skipped.find(
          (item) => item.reason === "duplicate_maturity_input",
        )?.maturity_input_hash,
        first.mature_events[0].maturity_input_hash,
      );
      assert.equal(matureEvents(await readOfferEvents(dataDir)).length, 1);
    },
    { shopifyWebhookSecret: WEBHOOK_SECRET },
  );
});

test("new refund after maturity changes input hash and allows corrected maturity", async () => {
  await withServer(
    async ({ baseUrl, dataDir }) => {
      await writeMarginConfig(dataDir);
      await createPaidTransaction(baseUrl);

      const first = await runMaturity(dataDir);
      await postRefund(baseUrl, "delivery_refund_after_mature", "15.00", {
        id: 987654323,
        admin_graphql_api_id: "gid://shopify/Refund/987654323",
      });
      const staleInboxResponse = await fetch(
        `${baseUrl}/counterpilot/merchant/offers?shop=counterpilot-dev.myshopify.com`,
      );
      const staleInbox = await staleInboxResponse.json();
      assert.equal(staleInbox.offers[0].lifecycle_state, "partially_refunded");
      assert.equal("mature_margin_minor" in staleInbox.offers[0], false);
      assert.equal("maturity_input_hash" in staleInbox.offers[0], false);

      const second = await runMaturity(dataDir);
      assert.equal(first.appended, 1);
      assert.equal(second.appended, 1);
      assert.notEqual(
        first.mature_events[0].maturity_input_hash,
        second.mature_events[0].maturity_input_hash,
      );

      const events = await readOfferEvents(dataDir);
      const mature = matureEvents(events);
      assert.equal(mature.length, 2);
      assert.equal(mature.at(-1).refund_total_minor, 1500);
      assert.equal(mature.at(-1).mature_margin_minor, 14000);

      const inboxResponse = await fetch(
        `${baseUrl}/counterpilot/merchant/offers?shop=counterpilot-dev.myshopify.com`,
      );
      const inbox = await inboxResponse.json();
      assert.equal(inbox.offers[0].lifecycle_state, "mature");
      assert.equal(inbox.offers[0].mature_margin_minor, 14000);
      assertNoRawMaturityLeak(inbox);
    },
    { shopifyWebhookSecret: WEBHOOK_SECRET },
  );
});

test("transactions without paid event and transactions with holds are skipped", async () => {
  await withServer(
    async ({ baseUrl, dataDir }) => {
      await writeMarginConfig(dataDir);
      await createOrderButNotPaidTransaction(baseUrl);
      const notPaid = await runMaturity(dataDir);
      assert.equal(notPaid.appended, 0);
      assert.ok(notPaid.skipped.some((item) => item.reason === "not_paid"));
    },
    { shopifyWebhookSecret: WEBHOOK_SECRET },
  );

  await withServer(
    async ({ baseUrl, dataDir }) => {
      await writeMarginConfig(dataDir);
      await createPaidTransaction(baseUrl);
      const response = await postShopifyWebhook(baseUrl, {
        topic: "refunds/create",
        deliveryId: "delivery_refund_reconciliation_hold",
        pathName: "refunds",
        payload: refundWebhookPayload({
          transactions: [
            {
              id: 444555667,
              kind: "refund",
              status: "success",
              amount: "15.00",
            },
          ],
        }),
      });
      assert.equal(response.status, 202);

      const held = await runMaturity(dataDir);
      assert.equal(held.appended, 0);
      assert.ok(
        held.skipped.some((item) => item.reason === "reconciliation_hold"),
      );
    },
    { shopifyWebhookSecret: WEBHOOK_SECRET },
  );
});
