import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  calculateAppProxySignature,
  createCounterpilotServer,
} from "./counterpilot-server.mjs";

const WEBHOOK_SECRET = "shopify-webhook-secret";

async function withServer(fn, options = {}) {
  const dataDir = await fs.mkdtemp(
    path.join(os.tmpdir(), "counterpilot-server-"),
  );
  const server = createCounterpilotServer({ dataDir, ...options });
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

async function postJson(url, payload, headers = {}) {
  return fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(payload),
  });
}

async function postOffer(baseUrl, payload = validOffer()) {
  const response = await postJson(`${baseUrl}/counterpilot/offers`, payload);
  const body = await response.json();
  return { response, body };
}

async function submitOffer(baseUrl, payload = validOffer()) {
  const { response, body } = await postOffer(baseUrl, payload);
  assert.equal(response.status, 201);
  return body.transaction_id;
}

async function merchantAction(
  baseUrl,
  transactionId,
  action,
  payload,
  headers = {},
) {
  const response = await postJson(
    `${baseUrl}/counterpilot/merchant/offers/${encodeURIComponent(transactionId)}/${action}`,
    payload,
    headers,
  );
  const body = await response.json();
  return { response, body };
}

function buyerUrl(
  baseUrl,
  buyerResponsePath,
  shop = "counterpilot-dev.myshopify.com",
) {
  const url = new URL(buyerResponsePath, baseUrl);
  url.searchParams.set("shop", shop);
  return url.toString();
}

function buyerAcceptUrl(
  baseUrl,
  buyerResponsePath,
  shop = "counterpilot-dev.myshopify.com",
) {
  return buyerUrl(
    baseUrl,
    buyerResponsePath.replace("/respond?", "/accept?"),
    shop,
  );
}

function tokenFromBuyerResponsePath(buyerResponsePath) {
  return new URL(
    buyerResponsePath,
    "http://counterpilot.local",
  ).searchParams.get("token");
}

function createFakeShopifyAdapter(options = {}) {
  const calls = [];
  const adapter = async (payload) => {
    calls.push(payload);
    if (options.error) {
      throw options.error;
    }
    return {
      draftOrderId:
        options.draftOrderId ?? "gid://shopify/DraftOrder/checkout-created-1",
      checkoutUrl:
        options.checkoutUrl ??
        "https://checkout.counterpilot.test/invoice/checkout-created-1",
    };
  };
  return { adapter, calls };
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

async function postShopifyWebhook(
  baseUrl,
  { topic, deliveryId, payload, secret = WEBHOOK_SECRET, headers = {} },
) {
  const rawBody = JSON.stringify(payload);
  return fetch(`${baseUrl}/counterpilot/webhooks/shopify/orders`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-shopify-topic": topic,
      "x-shopify-webhook-id": deliveryId,
      "x-shopify-shop-domain": "counterpilot-dev.myshopify.com",
      "x-shopify-hmac-sha256": signWebhookBody(rawBody, secret),
      ...headers,
    },
    body: rawBody,
  });
}

async function createCheckoutTransaction(baseUrl) {
  const transactionId = await submitOffer(baseUrl);
  const accepted = await merchantAction(baseUrl, transactionId, "accept", {
    store_id: "counterpilot-dev.myshopify.com",
  });
  assert.equal(accepted.response.status, 200);
  const checkout = await fetch(
    buyerAcceptUrl(baseUrl, accepted.body.buyer_response_path),
    { method: "POST" },
  );
  assert.equal(checkout.status, 200);
  return transactionId;
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

async function readCheckoutRefs(dataDir) {
  const persisted = await fs.readFile(
    path.join(dataDir, "checkout_refs.jsonl"),
    "utf8",
  );
  return persisted
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

async function readOrderRefs(dataDir) {
  const persisted = await fs.readFile(
    path.join(dataDir, "order_refs.jsonl"),
    "utf8",
  );
  return persisted
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

async function readWebhookDeliveries(dataDir) {
  const persisted = await fs.readFile(
    path.join(dataDir, "shopify_webhook_deliveries.jsonl"),
    "utf8",
  );
  return persisted
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

function assertSafeArtifact(value) {
  const text = JSON.stringify(value);
  assert.doesNotMatch(text, /buyer@example\.com/);
  assert.doesNotMatch(text, /gid:\/\/shopify\/Product/);
  assert.doesNotMatch(text, /gid:\/\/shopify\/DraftOrder/);
  assert.doesNotMatch(text, /gid:\/\/shopify\/Order/);
  assert.doesNotMatch(text, /https:\/\/checkout\.counterpilot\.test/);
  assert.doesNotMatch(text, /raw-status-token/);
  assert.doesNotMatch(text, /123 Union/);
  assert.doesNotMatch(text, /phone/i);
  assert.doesNotMatch(text, /shipping-address/i);
  assert.doesNotMatch(text, /secret-token/i);
}

function assertNoRawOrderWebhookLeak(value) {
  const text = JSON.stringify(value);
  assert.doesNotMatch(text, /buyer@example\.com/);
  assert.doesNotMatch(text, /gid:\/\/shopify\/Order/);
  assert.doesNotMatch(text, /raw-status-token/);
  assert.doesNotMatch(text, /123 Union/);
  assert.doesNotMatch(text, /555-0100/);
  assert.doesNotMatch(text, /contact_email/);
  assert.doesNotMatch(text, /shipping_address/);
  assert.doesNotMatch(text, /customer/);
}

function assertBuyerCheckoutResponse(value) {
  const text = JSON.stringify(value);
  assert.doesNotMatch(text, /buyer@example\.com/);
  assert.doesNotMatch(text, /gid:\/\/shopify\/Product/);
  assert.doesNotMatch(text, /gid:\/\/shopify\/DraftOrder/);
  assert.doesNotMatch(text, /phone/i);
  assert.doesNotMatch(text, /shipping-address/i);
  assert.doesNotMatch(text, /secret-token/i);
  assert.match(value.checkout_url, /^https:\/\/checkout\.counterpilot\.test\//);
}

test("POST /counterpilot/offers persists an offer_submitted event and returns only safe fields", async () => {
  await withServer(async ({ baseUrl, dataDir }) => {
    const { response, body } = await postOffer(baseUrl);
    assert.equal(response.status, 201);
    assert.equal(body.received, true);
    assert.equal(body.lifecycle_state, "offer_submitted");
    assert.equal(body.offer_amount_minor, 61000);
    assert.equal(body.currency, "USD");
    assert.match(body.transaction_id, /^cp_offer_/);
    assertSafeArtifact(body);

    const events = await readOfferEvents(dataDir);
    assert.equal(events.length, 1);
    assert.equal(events[0].schema_version, "counterpilot.offer_event.v1");
    assert.equal(events[0].lifecycle_state, "offer_submitted");
    assert.equal(events[0].event_type, "offer_submitted");
    assert.equal(events[0].actor_type, "buyer");
    assert.equal(
      events[0].operational_refs.product_ref,
      "gid://shopify/Product/123",
    );
    assert.doesNotMatch(JSON.stringify(events), /buyer@example\.com/);
    assert.doesNotMatch(JSON.stringify(events), /checkout/i);
  });
});

test("POST /apps/counterpilot/offers derives the store from app-proxy query parameters", async () => {
  await withServer(async ({ baseUrl }) => {
    const response = await postJson(
      `${baseUrl}/apps/counterpilot/offers?shop=counterpilot-dev.myshopify.com&timestamp=1`,
      validOffer({ shop: "attacker.myshopify.com" }),
    );
    assert.equal(response.status, 403);

    const accepted = await postJson(
      `${baseUrl}/apps/counterpilot/offers?shop=counterpilot-dev.myshopify.com&timestamp=1`,
      validOffer({ shop: "counterpilot-dev.myshopify.com" }),
    );
    assert.equal(accepted.status, 201);
  });
});

test("POST /apps/counterpilot/offers verifies app-proxy signatures when a secret is configured", async () => {
  const appProxySecret = "test-secret";
  await withServer(
    async ({ baseUrl }) => {
      const unsigned = await postJson(
        `${baseUrl}/apps/counterpilot/offers?shop=counterpilot-dev.myshopify.com&timestamp=1`,
        validOffer(),
      );
      assert.equal(unsigned.status, 401);

      const params = new URLSearchParams({
        path_prefix: "/apps/counterpilot",
        shop: "counterpilot-dev.myshopify.com",
        timestamp: "1",
      });
      params.set(
        "signature",
        calculateAppProxySignature(params, appProxySecret),
      );
      const signed = await postJson(
        `${baseUrl}/apps/counterpilot/offers?${params}`,
        validOffer(),
      );
      assert.equal(signed.status, 201);
    },
    { appProxySecret },
  );
});

test("GET /counterpilot/merchant/offers lists current offer states without raw buyer or Shopify references", async () => {
  await withServer(async ({ baseUrl }) => {
    const transactionId = await submitOffer(baseUrl);
    const { response } = await merchantAction(
      baseUrl,
      transactionId,
      "counter",
      {
        store_id: "counterpilot-dev.myshopify.com",
        counter_amount: "625.00",
        currency: "USD",
      },
    );
    assert.equal(response.status, 200);

    const inboxResponse = await fetch(
      `${baseUrl}/counterpilot/merchant/offers?shop=counterpilot-dev.myshopify.com`,
    );
    assert.equal(inboxResponse.status, 200);
    const body = await inboxResponse.json();
    assert.equal(body.count, 1);
    assert.equal(body.offers[0].lifecycle_state, "merchant_countered");
    assert.equal(body.offers[0].product_title, "The Complete Snowboard");
    assert.equal(body.offers[0].offer_amount_minor, 61000);
    assert.equal(body.offers[0].counter_amount_minor, 62500);
    assert.equal(body.offers[0].counter_currency, "USD");
    assert.equal(body.offers[0].quantity, 1);
    assert.ok(body.offers[0].product_reference_hash.startsWith("sha256:"));
    assertSafeArtifact(body);
  });
});

test("GET /counterpilot/merchant/offers/:transaction_id returns a sanitized current-state detail", async () => {
  await withServer(async ({ baseUrl }) => {
    const transactionId = await submitOffer(baseUrl);
    const detailResponse = await fetch(
      `${baseUrl}/counterpilot/merchant/offers/${transactionId}?store_id=counterpilot-dev.myshopify.com`,
    );
    assert.equal(detailResponse.status, 200);
    const detail = await detailResponse.json();
    assert.equal(detail.offer.transaction_id, transactionId);
    assert.equal(detail.offer.lifecycle_state, "offer_submitted");
    assertSafeArtifact(detail);
  });
});

test("submitted offers can be accepted manually", async () => {
  await withServer(async ({ baseUrl, dataDir }) => {
    const transactionId = await submitOffer(baseUrl);
    const { response, body } = await merchantAction(
      baseUrl,
      transactionId,
      "accept",
      {
        store_id: "counterpilot-dev.myshopify.com",
      },
    );
    assert.equal(response.status, 200);
    assert.equal(body.offer.lifecycle_state, "merchant_accepted");
    assert.match(
      body.buyer_response_path,
      /^\/apps\/counterpilot\/offers\/cp_offer_.*\/respond\?token=/,
    );
    assertSafeArtifact(body);

    const events = await readOfferEvents(dataDir);
    assert.equal(
      events.map((event) => event.event_type).join(","),
      "offer_submitted,merchant_accepted",
    );
    assert.equal(events[1].actor_type, "merchant");
    assert.ok(events[1].buyer_response_token_hash.startsWith("sha256:"));
    assert.ok(events[1].buyer_response_expires_at);
    assert.doesNotMatch(
      JSON.stringify(events),
      new RegExp(tokenFromBuyerResponsePath(body.buyer_response_path)),
    );
  });
});

test("submitted offers can be countered manually", async () => {
  await withServer(async ({ baseUrl, dataDir }) => {
    const transactionId = await submitOffer(baseUrl);
    const { response, body } = await merchantAction(
      baseUrl,
      transactionId,
      "counter",
      {
        store_id: "counterpilot-dev.myshopify.com",
        counter_amount: "650.25",
        currency: "USD",
      },
    );
    assert.equal(response.status, 200);
    assert.equal(body.offer.lifecycle_state, "merchant_countered");
    assert.equal(body.offer.counter_amount_minor, 65025);
    assert.equal(body.offer.counter_currency, "USD");
    assert.match(
      body.buyer_response_path,
      /^\/apps\/counterpilot\/offers\/cp_offer_.*\/respond\?token=/,
    );
    assertSafeArtifact(body);

    const events = await readOfferEvents(dataDir);
    assert.equal(events[1].event_type, "merchant_countered");
    assert.equal(events[1].counter_amount_minor, 65025);
    assert.equal(events[1].currency, "USD");
    assert.ok(events[1].buyer_response_token_hash.startsWith("sha256:"));
    assert.ok(events[1].buyer_response_expires_at);
    assert.doesNotMatch(
      JSON.stringify(events),
      new RegExp(tokenFromBuyerResponsePath(body.buyer_response_path)),
    );
  });
});

test("submitted offers can be declined manually", async () => {
  await withServer(async ({ baseUrl, dataDir }) => {
    const transactionId = await submitOffer(baseUrl);
    const { response, body } = await merchantAction(
      baseUrl,
      transactionId,
      "decline",
      {
        store_id: "counterpilot-dev.myshopify.com",
      },
    );
    assert.equal(response.status, 200);
    assert.equal(body.offer.lifecycle_state, "merchant_declined");
    assert.equal(body.buyer_response_path, undefined);
    assertSafeArtifact(body);

    const events = await readOfferEvents(dataDir);
    assert.equal(events[1].event_type, "merchant_declined");
  });
});

test("buyer can fetch a safe response view for a countered offer", async () => {
  await withServer(async ({ baseUrl }) => {
    const transactionId = await submitOffer(
      baseUrl,
      validOffer({ offer_amount: "580.00" }),
    );
    const counter = await merchantAction(baseUrl, transactionId, "counter", {
      store_id: "counterpilot-dev.myshopify.com",
      counter_amount: "610.00",
      currency: "USD",
    });
    assert.equal(counter.response.status, 200);

    const response = await fetch(
      buyerUrl(baseUrl, counter.body.buyer_response_path),
    );
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.deepEqual(body, {
      transaction_id: transactionId,
      status: "merchant_countered",
      product_title: "The Complete Snowboard",
      original_offer_amount_minor: 58000,
      accepted_amount_minor: 61000,
      currency: "USD",
      quantity: 1,
      expires_at: body.expires_at,
    });
    assert.ok(body.expires_at);
    assertSafeArtifact(body);
  });
});

test("buyer can accept a merchant counter and create a checkout", async () => {
  const shopify = createFakeShopifyAdapter();
  await withServer(
    async ({ baseUrl, dataDir }) => {
      const transactionId = await submitOffer(
        baseUrl,
        validOffer({ offer_amount: "580.00", quantity: 2 }),
      );
      const counter = await merchantAction(baseUrl, transactionId, "counter", {
        store_id: "counterpilot-dev.myshopify.com",
        counter_amount: "610.00",
        currency: "USD",
      });
      assert.equal(counter.response.status, 200);
      assert.equal(shopify.calls.length, 0);

      const response = await fetch(
        buyerAcceptUrl(baseUrl, counter.body.buyer_response_path),
        { method: "POST" },
      );
      assert.equal(response.status, 200);
      const body = await response.json();
      assert.equal(body.offer.lifecycle_state, "checkout_created");
      assert.equal(body.offer.accepted_amount_minor, 61000);
      assert.equal(body.offer.accepted_currency, "USD");
      assert.equal(body.offer.negotiated_revenue_minor, 122000);
      assertBuyerCheckoutResponse(body);

      assert.equal(shopify.calls.length, 1);
      assert.deepEqual(shopify.calls[0], {
        shop: "counterpilot-dev.myshopify.com",
        adminAccessToken: undefined,
        apiVersion: "2026-04",
        transactionId,
        variantRef: "gid://shopify/ProductVariant/456",
        quantity: 2,
        acceptedUnitAmountMinor: 61000,
        currency: "USD",
        productTitle: "The Complete Snowboard",
      });

      const events = await readOfferEvents(dataDir);
      assert.equal(
        events.map((event) => event.event_type).join(","),
        "offer_submitted,merchant_countered,buyer_accepted,checkout_creation_started,checkout_created",
      );
      assert.equal(events[2].accepted_amount_minor, 61000);
      assert.equal(events[2].currency, "USD");
      assert.equal(events[2].accepted_from_event_type, "merchant_countered");
      assert.equal(events[3].event_type, "checkout_creation_started");
      assert.ok(
        events[3].checkout_request_reference_hash.startsWith("sha256:"),
      );
      assert.equal(events[4].accepted_amount_minor, 61000);
      assert.equal(events[4].quantity, 2);
      assert.equal(events[4].negotiated_revenue_minor, 122000);
      assert.ok(events[4].draft_order_reference_hash.startsWith("sha256:"));
      assert.ok(events[4].checkout_reference_hash.startsWith("sha256:"));
      assert.doesNotMatch(
        JSON.stringify(events),
        /https:\/\/checkout\.counterpilot\.test/,
      );
      assert.doesNotMatch(
        JSON.stringify(events),
        /gid:\/\/shopify\/DraftOrder/,
      );

      const checkoutRefs = await readCheckoutRefs(dataDir);
      assert.equal(checkoutRefs.length, 1);
      assert.equal(checkoutRefs[0].checkout_url, body.checkout_url);
      assert.equal(
        checkoutRefs[0].draft_order_id,
        "gid://shopify/DraftOrder/checkout-created-1",
      );

      const inboxResponse = await fetch(
        `${baseUrl}/counterpilot/merchant/offers?shop=counterpilot-dev.myshopify.com`,
      );
      assert.equal(inboxResponse.status, 200);
      const inbox = await inboxResponse.json();
      assert.equal(inbox.offers[0].lifecycle_state, "checkout_created");
      assert.equal(inbox.offers[0].accepted_amount_minor, 61000);
      assert.equal(
        inbox.offers[0].checkout_reference_hash,
        events[4].checkout_reference_hash,
      );
      assertSafeArtifact(inbox);
    },
    { shopifyDraftOrderAdapter: shopify.adapter },
  );
});

test("buyer can accept a merchant acceptance of the original offer and create a checkout", async () => {
  const shopify = createFakeShopifyAdapter();
  await withServer(
    async ({ baseUrl, dataDir }) => {
      const transactionId = await submitOffer(
        baseUrl,
        validOffer({ offer_amount: "590.00" }),
      );
      const accepted = await merchantAction(baseUrl, transactionId, "accept", {
        store_id: "counterpilot-dev.myshopify.com",
      });
      assert.equal(accepted.response.status, 200);
      assert.equal(shopify.calls.length, 0);

      const response = await fetch(
        buyerAcceptUrl(baseUrl, accepted.body.buyer_response_path),
        { method: "POST" },
      );
      assert.equal(response.status, 200);
      const body = await response.json();
      assert.equal(body.offer.lifecycle_state, "checkout_created");
      assert.equal(body.offer.accepted_amount_minor, 59000);
      assertBuyerCheckoutResponse(body);

      assert.equal(shopify.calls.length, 1);
      assert.equal(shopify.calls[0].acceptedUnitAmountMinor, 59000);

      const events = await readOfferEvents(dataDir);
      assert.equal(events[2].event_type, "buyer_accepted");
      assert.equal(events[2].accepted_amount_minor, 59000);
      assert.equal(events[2].accepted_from_event_type, "merchant_accepted");
      assert.equal(events[3].event_type, "checkout_creation_started");
      assert.equal(events[4].event_type, "checkout_created");
    },
    { shopifyDraftOrderAdapter: shopify.adapter },
  );
});

test("buyer acceptance rejects declined, pre-action, and unknown transactions before checkout", async () => {
  const shopify = createFakeShopifyAdapter();
  await withServer(
    async ({ baseUrl }) => {
      const submittedId = await submitOffer(baseUrl);
      const submittedResponse = await fetch(
        `${baseUrl}/apps/counterpilot/offers/${submittedId}/accept?shop=counterpilot-dev.myshopify.com&token=anything`,
        { method: "POST" },
      );
      assert.equal(submittedResponse.status, 409);

      const declinedId = await submitOffer(baseUrl);
      const declined = await merchantAction(baseUrl, declinedId, "decline", {
        store_id: "counterpilot-dev.myshopify.com",
      });
      assert.equal(declined.response.status, 200);
      const declinedResponse = await fetch(
        `${baseUrl}/apps/counterpilot/offers/${declinedId}/accept?shop=counterpilot-dev.myshopify.com&token=anything`,
        { method: "POST" },
      );
      assert.equal(declinedResponse.status, 409);

      const unknownResponse = await fetch(
        `${baseUrl}/apps/counterpilot/offers/cp_offer_missing/accept?shop=counterpilot-dev.myshopify.com&token=anything`,
        { method: "POST" },
      );
      assert.equal(unknownResponse.status, 404);
      assert.equal(shopify.calls.length, 0);
    },
    { shopifyDraftOrderAdapter: shopify.adapter },
  );
});

test("buyer acceptance retry returns the existing checkout without duplicate events or draft orders", async () => {
  const shopify = createFakeShopifyAdapter();
  await withServer(
    async ({ baseUrl, dataDir }) => {
      const acceptedId = await submitOffer(baseUrl);
      const merchantAccepted = await merchantAction(
        baseUrl,
        acceptedId,
        "accept",
        {
          store_id: "counterpilot-dev.myshopify.com",
        },
      );
      const firstAccept = await fetch(
        buyerAcceptUrl(baseUrl, merchantAccepted.body.buyer_response_path),
        { method: "POST" },
      );
      assert.equal(firstAccept.status, 200);
      const firstBody = await firstAccept.json();

      const secondAccept = await fetch(
        buyerAcceptUrl(baseUrl, merchantAccepted.body.buyer_response_path),
        { method: "POST" },
      );
      assert.equal(secondAccept.status, 200);
      const secondBody = await secondAccept.json();
      assert.equal(secondBody.checkout_url, firstBody.checkout_url);
      assert.equal(secondBody.offer.lifecycle_state, "checkout_created");
      assertBuyerCheckoutResponse(secondBody);

      assert.equal(shopify.calls.length, 1);
      const events = await readOfferEvents(dataDir);
      assert.equal(
        events.filter((event) => event.event_type === "buyer_accepted").length,
        1,
      );
      assert.equal(
        events.filter((event) => event.event_type === "checkout_created")
          .length,
        1,
      );
      assert.equal(
        events.filter(
          (event) => event.event_type === "checkout_creation_started",
        ).length,
        1,
      );
      const checkoutRefs = await readCheckoutRefs(dataDir);
      assert.equal(checkoutRefs.length, 1);
    },
    { shopifyDraftOrderAdapter: shopify.adapter },
  );
});

test("buyer acceptance with a pending checkout marker does not create a duplicate draft order", async () => {
  const shopify = createFakeShopifyAdapter();
  await withServer(
    async ({ baseUrl, dataDir }) => {
      const transactionId = await submitOffer(baseUrl);
      const accepted = await merchantAction(baseUrl, transactionId, "accept", {
        store_id: "counterpilot-dev.myshopify.com",
      });
      const persistedEvents = await readOfferEvents(dataDir);
      const merchantAccepted = persistedEvents.at(-1);
      const acceptedAmountMinor = 61000;
      const acceptedCurrency = "USD";
      const quantity = 1;
      const checkoutRequestHash = crypto
        .createHash("sha256")
        .update(
          [transactionId, acceptedAmountMinor, acceptedCurrency, quantity].join(
            ":",
          ),
        )
        .digest("hex");
      const buyerAccepted = {
        schema_version: "counterpilot.offer_event.v1",
        transaction_id: transactionId,
        lifecycle_state: "buyer_accepted",
        event_type: "buyer_accepted",
        actor_type: "buyer",
        occurred_at: "2026-06-23T10:00:00.000Z",
        store_id: "counterpilot-dev.myshopify.com",
        store_reference_hash: merchantAccepted.store_reference_hash,
        source: "counterpilot_buyer_response",
        accepted_amount_minor: acceptedAmountMinor,
        currency: acceptedCurrency,
        accepted_from_event_type: "merchant_accepted",
      };
      const checkoutStarted = {
        schema_version: "counterpilot.offer_event.v1",
        transaction_id: transactionId,
        lifecycle_state: "buyer_accepted",
        event_type: "checkout_creation_started",
        actor_type: "system",
        occurred_at: "2026-06-23T10:00:01.000Z",
        store_id: "counterpilot-dev.myshopify.com",
        store_reference_hash: merchantAccepted.store_reference_hash,
        source: "counterpilot_shopify_draft_order",
        accepted_amount_minor: acceptedAmountMinor,
        currency: acceptedCurrency,
        quantity,
        checkout_request_reference_hash: `sha256:${checkoutRequestHash}`,
      };
      await fs.appendFile(
        path.join(dataDir, "offers.jsonl"),
        `${JSON.stringify(buyerAccepted)}\n${JSON.stringify(checkoutStarted)}\n`,
        "utf8",
      );

      const retry = await fetch(
        buyerAcceptUrl(baseUrl, accepted.body.buyer_response_path),
        { method: "POST" },
      );
      assert.equal(retry.status, 202);
      const body = await retry.json();
      assert.equal(body.error, "checkout_creation_pending");
      assert.equal(body.offer.lifecycle_state, "buyer_accepted");
      assert.equal(body.offer.checkout_creation_status, "started");
      assertSafeArtifact(body);
      assert.equal(shopify.calls.length, 0);

      const events = await readOfferEvents(dataDir);
      assert.equal(
        events.filter((event) => event.event_type === "buyer_accepted").length,
        1,
      );
      assert.equal(
        events.filter(
          (event) => event.event_type === "checkout_creation_started",
        ).length,
        1,
      );
      assert.equal(
        events.filter((event) => event.event_type === "checkout_created")
          .length,
        0,
      );
    },
    { shopifyDraftOrderAdapter: shopify.adapter },
  );
});

test("valid orders/create webhook appends order_created and stores raw order refs only operationally", async () => {
  const shopify = createFakeShopifyAdapter();
  await withServer(
    async ({ baseUrl, dataDir }) => {
      const transactionId = await createCheckoutTransaction(baseUrl);
      const response = await postShopifyWebhook(baseUrl, {
        topic: "orders/create",
        deliveryId: "delivery_order_create_1",
        payload: orderWebhookPayload(transactionId),
      });
      assert.equal(response.status, 200);
      const body = await response.json();
      assert.deepEqual(body.appended, { order_created: true, paid: false });
      assert.equal(body.lifecycle_state, "order_created");

      const events = await readOfferEvents(dataDir);
      assert.equal(events.at(-1).event_type, "order_created");
      assert.equal(events.at(-1).lifecycle_state, "order_created");
      assert.equal(events.at(-1).order_total_minor, 61000);
      assert.equal(events.at(-1).shipping_total_minor, 0);
      assert.equal(events.at(-1).tax_total_minor, 0);
      assert.equal(events.at(-1).discount_total_minor, 0);
      assert.equal(events.at(-1).currency, "USD");
      assert.equal(events.at(-1).production_evidence, false);
      assert.ok(events.at(-1).order_reference_hash.startsWith("sha256:"));
      assert.ok(events.at(-1).order_name_reference_hash.startsWith("sha256:"));
      assertNoRawOrderWebhookLeak(events);

      const orderRefs = await readOrderRefs(dataDir);
      assert.equal(orderRefs.length, 1);
      assert.equal(orderRefs[0].transaction_id, transactionId);
      assert.equal(
        orderRefs[0].admin_graphql_api_id,
        "gid://shopify/Order/123456789",
      );
      assert.equal(orderRefs[0].order_name, "#1001");
      assert.doesNotMatch(JSON.stringify(orderRefs), /buyer@example\.com/);
      assert.doesNotMatch(JSON.stringify(orderRefs), /raw-status-token/);
      assert.doesNotMatch(JSON.stringify(orderRefs), /123 Union/);

      const deliveries = await readWebhookDeliveries(dataDir);
      assert.equal(deliveries.length, 1);
      assert.equal(deliveries[0].delivery_id, "delivery_order_create_1");
      assert.equal(deliveries[0].status, "processed");
      assertSafeArtifact(deliveries);

      const inboxResponse = await fetch(
        `${baseUrl}/counterpilot/merchant/offers?shop=counterpilot-dev.myshopify.com`,
      );
      assert.equal(inboxResponse.status, 200);
      const inbox = await inboxResponse.json();
      assert.equal(inbox.offers[0].lifecycle_state, "order_created");
      assert.equal(inbox.offers[0].order_total_minor, 61000);
      assertSafeArtifact(inbox);
      assert.equal(shopify.calls.length, 1);
    },
    {
      shopifyDraftOrderAdapter: shopify.adapter,
      shopifyWebhookSecret: WEBHOOK_SECRET,
    },
  );
});

test("orders/paid arriving before orders/create appends order_created then paid", async () => {
  const shopify = createFakeShopifyAdapter();
  await withServer(
    async ({ baseUrl, dataDir }) => {
      const transactionId = await createCheckoutTransaction(baseUrl);
      const response = await postShopifyWebhook(baseUrl, {
        topic: "orders/paid",
        deliveryId: "delivery_paid_first",
        payload: orderWebhookPayload(transactionId, {
          financial_status: "paid",
          processed_at: "2026-06-23T14:02:00-04:00",
        }),
      });
      assert.equal(response.status, 200);
      const body = await response.json();
      assert.deepEqual(body.appended, { order_created: true, paid: true });
      assert.equal(body.lifecycle_state, "paid");

      const events = await readOfferEvents(dataDir);
      assert.equal(
        events
          .filter((event) =>
            ["order_created", "paid"].includes(event.event_type),
          )
          .map((event) => event.event_type)
          .join(","),
        "order_created,paid",
      );
      const paid = events.at(-1);
      assert.equal(paid.event_type, "paid");
      assert.equal(paid.paid_total_minor, 61000);
      assert.equal(paid.currency, "USD");
      assert.equal(paid.production_evidence, false);
      assertNoRawOrderWebhookLeak(events);

      const orderRefs = await readOrderRefs(dataDir);
      assert.equal(orderRefs.length, 1);
      assert.equal(
        orderRefs[0].admin_graphql_api_id,
        "gid://shopify/Order/123456789",
      );
    },
    {
      shopifyDraftOrderAdapter: shopify.adapter,
      shopifyWebhookSecret: WEBHOOK_SECRET,
    },
  );
});

test("duplicate Shopify webhook delivery does not append duplicate order or paid events", async () => {
  const shopify = createFakeShopifyAdapter();
  await withServer(
    async ({ baseUrl, dataDir }) => {
      const transactionId = await createCheckoutTransaction(baseUrl);
      const payload = orderWebhookPayload(transactionId, {
        financial_status: "paid",
      });
      const first = await postShopifyWebhook(baseUrl, {
        topic: "orders/paid",
        deliveryId: "delivery_duplicate_paid",
        payload,
      });
      assert.equal(first.status, 200);
      const second = await postShopifyWebhook(baseUrl, {
        topic: "orders/paid",
        deliveryId: "delivery_duplicate_paid",
        payload,
      });
      assert.equal(second.status, 200);
      const secondBody = await second.json();
      assert.equal(secondBody.duplicate, true);

      const events = await readOfferEvents(dataDir);
      assert.equal(
        events.filter((event) => event.event_type === "order_created").length,
        1,
      );
      assert.equal(
        events.filter((event) => event.event_type === "paid").length,
        1,
      );
      const orderRefs = await readOrderRefs(dataDir);
      assert.equal(orderRefs.length, 1);
      const deliveries = await readWebhookDeliveries(dataDir);
      assert.equal(deliveries.length, 1);
    },
    {
      shopifyDraftOrderAdapter: shopify.adapter,
      shopifyWebhookSecret: WEBHOOK_SECRET,
    },
  );
});

test("invalid Shopify webhook HMAC is rejected before parsing payload", async () => {
  await withServer(
    async ({ baseUrl, dataDir }) => {
      const response = await fetch(
        `${baseUrl}/counterpilot/webhooks/shopify/orders`,
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-shopify-topic": "orders/create",
            "x-shopify-webhook-id": "delivery_bad_hmac",
            "x-shopify-shop-domain": "counterpilot-dev.myshopify.com",
            "x-shopify-hmac-sha256": signWebhookBody(
              Buffer.from("different body"),
            ),
          },
          body: "{not valid json",
        },
      );
      assert.equal(response.status, 401);
      await assert.rejects(
        fs.readFile(path.join(dataDir, "offers.jsonl"), "utf8"),
        { code: "ENOENT" },
      );
      await assert.rejects(
        fs.readFile(
          path.join(dataDir, "shopify_webhook_deliveries.jsonl"),
          "utf8",
        ),
        { code: "ENOENT" },
      );
    },
    { shopifyWebhookSecret: WEBHOOK_SECRET },
  );
});

test("Shopify order webhooks with no Counterpilot transaction are ignored and deduped", async () => {
  await withServer(
    async ({ baseUrl, dataDir }) => {
      const response = await postShopifyWebhook(baseUrl, {
        topic: "orders/create",
        deliveryId: "delivery_no_transaction",
        payload: orderWebhookPayload(null),
      });
      assert.equal(response.status, 200);
      const body = await response.json();
      assert.equal(body.ignored, true);

      await assert.rejects(
        fs.readFile(path.join(dataDir, "offers.jsonl"), "utf8"),
        { code: "ENOENT" },
      );
      const deliveries = await readWebhookDeliveries(dataDir);
      assert.equal(deliveries.length, 1);
      assert.equal(deliveries[0].status, "ignored_no_counterpilot_transaction");
      assertSafeArtifact(deliveries);
    },
    { shopifyWebhookSecret: WEBHOOK_SECRET },
  );
});

test("Shopify order webhooks for unknown Counterpilot transactions are ignored safely", async () => {
  await withServer(
    async ({ baseUrl, dataDir }) => {
      const response = await postShopifyWebhook(baseUrl, {
        topic: "orders/create",
        deliveryId: "delivery_unknown_transaction",
        payload: orderWebhookPayload("cp_offer_missing"),
      });
      assert.equal(response.status, 200);
      const body = await response.json();
      assert.equal(body.ignored, true);

      await assert.rejects(
        fs.readFile(path.join(dataDir, "offers.jsonl"), "utf8"),
        { code: "ENOENT" },
      );
      const deliveries = await readWebhookDeliveries(dataDir);
      assert.equal(deliveries.length, 1);
      assert.equal(deliveries[0].status, "ignored_unknown_transaction");
      assertSafeArtifact(deliveries);
    },
    { shopifyWebhookSecret: WEBHOOK_SECRET },
  );
});

test("Shopify order webhook for a transaction without checkout_created is rejected without consuming delivery", async () => {
  await withServer(
    async ({ baseUrl, dataDir }) => {
      const transactionId = await submitOffer(baseUrl);
      const accepted = await merchantAction(baseUrl, transactionId, "accept", {
        store_id: "counterpilot-dev.myshopify.com",
      });
      assert.equal(accepted.response.status, 200);

      const response = await postShopifyWebhook(baseUrl, {
        topic: "orders/create",
        deliveryId: "delivery_before_checkout",
        payload: orderWebhookPayload(transactionId),
      });
      assert.equal(response.status, 409);
      const events = await readOfferEvents(dataDir);
      assert.equal(
        events.filter((event) => event.event_type === "order_created").length,
        0,
      );
      await assert.rejects(
        fs.readFile(
          path.join(dataDir, "shopify_webhook_deliveries.jsonl"),
          "utf8",
        ),
        { code: "ENOENT" },
      );
    },
    { shopifyWebhookSecret: WEBHOOK_SECRET },
  );
});

test("buyer acceptance rejects wrong store, wrong token, and expired token", async () => {
  await withServer(async ({ baseUrl }) => {
    const transactionId = await submitOffer(baseUrl);
    const counter = await merchantAction(baseUrl, transactionId, "counter", {
      store_id: "counterpilot-dev.myshopify.com",
      counter_amount: "625.00",
      currency: "USD",
    });
    assert.equal(counter.response.status, 200);

    const wrongStore = await fetch(
      buyerAcceptUrl(
        baseUrl,
        counter.body.buyer_response_path,
        "wrong.myshopify.com",
      ),
      { method: "POST" },
    );
    assert.equal(wrongStore.status, 403);

    const wrongTokenUrl = new URL(
      buyerAcceptUrl(baseUrl, counter.body.buyer_response_path),
    );
    wrongTokenUrl.searchParams.set("token", "wrong-token");
    const wrongToken = await fetch(wrongTokenUrl, { method: "POST" });
    assert.equal(wrongToken.status, 401);
  });

  await withServer(
    async ({ baseUrl }) => {
      const transactionId = await submitOffer(baseUrl);
      const counter = await merchantAction(baseUrl, transactionId, "counter", {
        store_id: "counterpilot-dev.myshopify.com",
        counter_amount: "625.00",
        currency: "USD",
      });
      assert.equal(counter.response.status, 200);

      const expired = await fetch(
        buyerAcceptUrl(baseUrl, counter.body.buyer_response_path),
        { method: "POST" },
      );
      assert.equal(expired.status, 410);
    },
    { buyerResponseTtlMs: -1 },
  );
});

test("accepted offer with missing variant_ref appends a safe checkout failure without calling Shopify", async () => {
  const shopify = createFakeShopifyAdapter();
  await withServer(
    async ({ baseUrl, dataDir }) => {
      const transactionId = await submitOffer(
        baseUrl,
        validOffer({ variant_gid: "" }),
      );
      const accepted = await merchantAction(baseUrl, transactionId, "accept", {
        store_id: "counterpilot-dev.myshopify.com",
      });
      assert.equal(accepted.response.status, 200);

      const response = await fetch(
        buyerAcceptUrl(baseUrl, accepted.body.buyer_response_path),
        { method: "POST" },
      );
      assert.equal(response.status, 422);
      const body = await response.json();
      assert.equal(body.error, "checkout_creation_failed");
      assert.equal(body.error_code, "missing_variant_ref");
      assert.equal(body.offer.lifecycle_state, "buyer_accepted");
      assertSafeArtifact(body);
      assert.equal(shopify.calls.length, 0);

      const events = await readOfferEvents(dataDir);
      assert.equal(
        events.map((event) => event.event_type).join(","),
        "offer_submitted,merchant_accepted,buyer_accepted,checkout_creation_failed",
      );
      assert.equal(events[3].error_code, "missing_variant_ref");
      assert.doesNotMatch(
        JSON.stringify(events),
        /https:\/\/checkout\.counterpilot\.test/,
      );
    },
    { shopifyDraftOrderAdapter: shopify.adapter },
  );
});

test("Shopify user errors are surfaced safely and can be retried without duplicate buyer acceptance", async () => {
  const shopifyError = Object.assign(new Error("raw Shopify payload hidden"), {
    code: "shopify_user_error",
    statusCode: 422,
    safeUserErrors: [{ field: "lineItems", message: "Variant is unavailable" }],
  });
  const calls = [];
  let shouldFail = true;
  const shopifyAdapter = async (payload) => {
    calls.push(payload);
    if (shouldFail) {
      shouldFail = false;
      throw shopifyError;
    }
    return {
      draftOrderId: "gid://shopify/DraftOrder/checkout-created-after-retry",
      checkoutUrl: "https://checkout.counterpilot.test/invoice/retry-success",
    };
  };

  await withServer(
    async ({ baseUrl, dataDir }) => {
      const transactionId = await submitOffer(baseUrl);
      const accepted = await merchantAction(baseUrl, transactionId, "accept", {
        store_id: "counterpilot-dev.myshopify.com",
      });
      const acceptUrl = buyerAcceptUrl(
        baseUrl,
        accepted.body.buyer_response_path,
      );
      const response = await fetch(acceptUrl, { method: "POST" });
      assert.equal(response.status, 422);
      const body = await response.json();
      assert.equal(body.error, "checkout_creation_failed");
      assert.equal(body.error_code, "shopify_user_error");
      assert.deepEqual(body.user_errors, [
        { field: "lineItems", message: "Variant is unavailable" },
      ]);
      assertSafeArtifact(body);
      assert.equal(calls.length, 1);

      const retry = await fetch(acceptUrl, { method: "POST" });
      assert.equal(retry.status, 200);
      const retryBody = await retry.json();
      assert.equal(retryBody.offer.lifecycle_state, "checkout_created");
      assert.equal(
        retryBody.checkout_url,
        "https://checkout.counterpilot.test/invoice/retry-success",
      );
      assertBuyerCheckoutResponse(retryBody);
      assert.equal(calls.length, 2);

      const events = await readOfferEvents(dataDir);
      assert.equal(
        events.filter((event) => event.event_type === "buyer_accepted").length,
        1,
      );
      assert.equal(
        events.filter((event) => event.event_type === "checkout_created")
          .length,
        1,
      );
      assert.equal(
        events.filter(
          (event) => event.event_type === "checkout_creation_started",
        ).length,
        2,
      );
      const failure = events.find(
        (event) => event.event_type === "checkout_creation_failed",
      );
      assert.equal(failure.error_code, "shopify_user_error");
      assert.deepEqual(failure.user_errors, [
        { field: "lineItems", message: "Variant is unavailable" },
      ]);
      assert.doesNotMatch(
        JSON.stringify(events),
        /https:\/\/checkout\.counterpilot\.test/,
      );
    },
    { shopifyDraftOrderAdapter: shopifyAdapter },
  );
});

test("merchant actions reject invalid transitions and unknown transactions", async () => {
  await withServer(async ({ baseUrl }) => {
    const declinedTransactionId = await submitOffer(baseUrl);
    const declined = await merchantAction(
      baseUrl,
      declinedTransactionId,
      "decline",
      {
        store_id: "counterpilot-dev.myshopify.com",
      },
    );
    assert.equal(declined.response.status, 200);
    const declinedCounter = await merchantAction(
      baseUrl,
      declinedTransactionId,
      "counter",
      {
        store_id: "counterpilot-dev.myshopify.com",
        counter_amount: "625.00",
        currency: "USD",
      },
    );
    assert.equal(declinedCounter.response.status, 409);

    const acceptedTransactionId = await submitOffer(baseUrl);
    const accepted = await merchantAction(
      baseUrl,
      acceptedTransactionId,
      "accept",
      {
        store_id: "counterpilot-dev.myshopify.com",
      },
    );
    assert.equal(accepted.response.status, 200);
    const acceptedCounter = await merchantAction(
      baseUrl,
      acceptedTransactionId,
      "counter",
      {
        store_id: "counterpilot-dev.myshopify.com",
        counter_amount: "625.00",
        currency: "USD",
      },
    );
    assert.equal(acceptedCounter.response.status, 409);

    const unknown = await merchantAction(
      baseUrl,
      "cp_offer_missing",
      "decline",
      {
        store_id: "counterpilot-dev.myshopify.com",
      },
    );
    assert.equal(unknown.response.status, 404);
  });
});

test("counter actions require positive same-currency counter amounts", async () => {
  await withServer(async ({ baseUrl }) => {
    for (const payload of [
      {
        store_id: "counterpilot-dev.myshopify.com",
        counter_amount: "0",
        currency: "USD",
      },
      {
        store_id: "counterpilot-dev.myshopify.com",
        counter_amount: "10.999",
        currency: "USD",
      },
      {
        store_id: "counterpilot-dev.myshopify.com",
        counter_amount: "625.00",
        currency: "EUR",
      },
    ]) {
      const transactionId = await submitOffer(baseUrl);
      const result = await merchantAction(
        baseUrl,
        transactionId,
        "counter",
        payload,
      );
      assert.equal(result.response.status, 400);
    }
  });
});

test("merchant actions reject notes, checkout URLs, phone numbers, and unexpected fields", async () => {
  await withServer(async ({ baseUrl }) => {
    for (const payload of [
      { store_id: "counterpilot-dev.myshopify.com", merchant_note: "Call me" },
      {
        store_id: "counterpilot-dev.myshopify.com",
        checkout_url: "https://example.com/checkout",
      },
      { store_id: "counterpilot-dev.myshopify.com", phone: "555-0100" },
      {
        store_id: "counterpilot-dev.myshopify.com",
        access_token: "secret-token",
      },
      {
        store_id: "counterpilot-dev.myshopify.com",
        unsupported_field: "not accepted",
      },
    ]) {
      const transactionId = await submitOffer(baseUrl);
      const result = await merchantAction(
        baseUrl,
        transactionId,
        "accept",
        payload,
      );
      assert.equal(result.response.status, 400);
    }
  });
});

test("public app-proxy paths do not expose merchant inbox or actions", async () => {
  await withServer(async ({ baseUrl }) => {
    const transactionId = await submitOffer(baseUrl);

    const inbox = await fetch(
      `${baseUrl}/apps/counterpilot/merchant/offers?shop=counterpilot-dev.myshopify.com`,
    );
    assert.equal(inbox.status, 404);

    const detail = await fetch(
      `${baseUrl}/apps/counterpilot/merchant/offers/${transactionId}?shop=counterpilot-dev.myshopify.com`,
    );
    assert.equal(detail.status, 404);

    const action = await postJson(
      `${baseUrl}/apps/counterpilot/merchant/offers/${transactionId}/accept`,
      { store_id: "counterpilot-dev.myshopify.com" },
    );
    assert.equal(action.status, 404);
  });
});

test("merchant routes require bearer authentication when a merchant auth token is configured", async () => {
  await withServer(
    async ({ baseUrl }) => {
      const transactionId = await submitOffer(baseUrl);

      const unauthenticatedInbox = await fetch(
        `${baseUrl}/counterpilot/merchant/offers?shop=counterpilot-dev.myshopify.com`,
      );
      assert.equal(unauthenticatedInbox.status, 401);

      const badAction = await merchantAction(baseUrl, transactionId, "accept", {
        store_id: "counterpilot-dev.myshopify.com",
      });
      assert.equal(badAction.response.status, 401);

      const goodAction = await merchantAction(
        baseUrl,
        transactionId,
        "accept",
        { store_id: "counterpilot-dev.myshopify.com" },
        { authorization: "Bearer merchant-secret" },
      );
      assert.equal(goodAction.response.status, 200);

      const authenticatedInbox = await fetch(
        `${baseUrl}/counterpilot/merchant/offers?shop=counterpilot-dev.myshopify.com`,
        { headers: { authorization: "Bearer merchant-secret" } },
      );
      assert.equal(authenticatedInbox.status, 200);
    },
    { merchantAuthToken: "merchant-secret" },
  );
});

test("offer intake rejects buyer messages, checkout URLs, phone numbers, and unknown fields", async () => {
  await withServer(async ({ baseUrl, dataDir }) => {
    for (const payload of [
      validOffer({ buyer_message: "Please ship quickly" }),
      validOffer({ checkout_url: "https://example.com/checkout" }),
      validOffer({ phone: "555-0100" }),
      validOffer({ unsupported_field: "not accepted" }),
    ]) {
      const response = await postJson(
        `${baseUrl}/counterpilot/offers`,
        payload,
      );
      assert.equal(response.status, 400);
    }

    await assert.rejects(
      fs.readFile(path.join(dataDir, "offers.jsonl"), "utf8"),
      { code: "ENOENT" },
    );
  });
});

test("offer intake validates amount, quantity, currency, product, store, and buyer contact", async () => {
  await withServer(async ({ baseUrl }) => {
    for (const payload of [
      validOffer({ offer_amount: "0" }),
      validOffer({ offer_amount: "10.999" }),
      validOffer({ quantity: 0 }),
      validOffer({ currency: "USDD" }),
      validOffer({ shop: " " }),
      validOffer({ product_gid: "" }),
      validOffer({ buyer_email: "" }),
      validOffer({
        buyer_email: "buyer@example.com",
        buyer_contact_token: "secret-token",
      }),
    ]) {
      const response = await postJson(
        `${baseUrl}/counterpilot/offers`,
        payload,
      );
      assert.equal(response.status, 400);
    }
  });
});

test("app-proxy signature helper follows Shopify's sorted query-string signing shape", () => {
  const params = new URLSearchParams();
  params.append("shop", "counterpilot-dev.myshopify.com");
  params.append("path_prefix", "/apps/counterpilot");
  params.append("extra", "1");
  params.append("extra", "2");
  params.append("timestamp", "1");
  params.append("signature", "ignored");

  const expectedMessage =
    "extra=1,2path_prefix=/apps/counterpilotshop=counterpilot-dev.myshopify.comtimestamp=1";
  const expected = crypto
    .createHmac("sha256", "secret")
    .update(expectedMessage, "utf8")
    .digest("hex");
  assert.equal(calculateAppProxySignature(params, "secret"), expected);
});
