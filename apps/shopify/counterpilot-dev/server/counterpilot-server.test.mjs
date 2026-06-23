import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { createCounterpilotServer } from "./counterpilot-server.mjs";

async function withServer(fn) {
  const dataDir = await fs.mkdtemp(path.join(os.tmpdir(), "counterpilot-server-"));
  const server = createCounterpilotServer({ dataDir });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const { port } = server.address();
  try {
    await fn({
      baseUrl: `http://127.0.0.1:${port}`,
      dataDir
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
    ...overrides
  };
}

async function postOffer(baseUrl, payload) {
  return fetch(`${baseUrl}/counterpilot/offers`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload)
  });
}

test("POST /counterpilot/offers persists an offer_submitted transaction and returns only safe fields", async () => {
  await withServer(async ({ baseUrl, dataDir }) => {
    const response = await postOffer(baseUrl, validOffer());
    assert.equal(response.status, 201);
    const body = await response.json();
    assert.equal(body.received, true);
    assert.equal(body.lifecycle_state, "offer_submitted");
    assert.equal(body.offer_amount_minor, 61000);
    assert.equal(body.currency, "USD");
    assert.match(body.transaction_id, /^cp_offer_/);

    const responseText = JSON.stringify(body);
    assert.doesNotMatch(responseText, /buyer@example\.com/);
    assert.doesNotMatch(responseText, /gid:\/\/shopify\/Product/);
    assert.doesNotMatch(responseText, /checkout/i);

    const persisted = await fs.readFile(path.join(dataDir, "offers.jsonl"), "utf8");
    assert.match(persisted, /"lifecycle_state":"offer_submitted"/);
    assert.match(persisted, /"event_type":"offer_submitted"/);
    assert.match(persisted, /gid:\/\/shopify\/Product\/123/);
    assert.doesNotMatch(persisted, /buyer@example\.com/);
    assert.doesNotMatch(persisted, /checkout/i);
  });
});

test("GET /counterpilot/merchant/offers lists pending offers without raw buyer or Shopify references", async () => {
  await withServer(async ({ baseUrl }) => {
    await postOffer(baseUrl, validOffer());

    const response = await fetch(`${baseUrl}/counterpilot/merchant/offers?shop=counterpilot-dev.myshopify.com`);
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.count, 1);
    assert.equal(body.offers[0].lifecycle_state, "offer_submitted");
    assert.equal(body.offers[0].product_title, "The Complete Snowboard");
    assert.equal(body.offers[0].offer_amount_minor, 61000);
    assert.equal(body.offers[0].quantity, 1);
    assert.ok(body.offers[0].product_reference_hash.startsWith("sha256:"));

    const inboxText = JSON.stringify(body);
    assert.doesNotMatch(inboxText, /buyer@example\.com/);
    assert.doesNotMatch(inboxText, /gid:\/\/shopify\/Product/);
    assert.doesNotMatch(inboxText, /checkout/i);
    assert.doesNotMatch(inboxText, /phone/i);
  });
});

test("offer intake rejects buyer messages, checkout URLs, phone numbers, and unknown fields", async () => {
  await withServer(async ({ baseUrl, dataDir }) => {
    for (const payload of [
      validOffer({ buyer_message: "Please ship quickly" }),
      validOffer({ checkout_url: "https://example.com/checkout" }),
      validOffer({ phone: "555-0100" }),
      validOffer({ unsupported_field: "not accepted" })
    ]) {
      const response = await postOffer(baseUrl, payload);
      assert.equal(response.status, 400);
    }

    await assert.rejects(
      fs.readFile(path.join(dataDir, "offers.jsonl"), "utf8"),
      { code: "ENOENT" }
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
      validOffer({ buyer_email: "buyer@example.com", buyer_contact_token: "token" })
    ]) {
      const response = await postOffer(baseUrl, payload);
      assert.equal(response.status, 400);
    }
  });
});
