import assert from "node:assert/strict";
import test from "node:test";

import {
  ShopifyDraftOrderError,
  buildDraftOrderInput,
  createDraftOrderForAcceptedOffer,
  formatMinorUnits,
} from "./shopify-draft-orders.mjs";

test("formatMinorUnits formats minor units as a Shopify MoneyInput decimal string", () => {
  assert.equal(formatMinorUnits(61000), "610.00");
  assert.equal(formatMinorUnits(1), "0.01");
  assert.throws(() => formatMinorUnits(0), ShopifyDraftOrderError);
});

test("buildDraftOrderInput uses variant priceOverride and disables discount stacking", () => {
  const input = buildDraftOrderInput({
    transactionId: "cp_offer_123",
    variantRef: "gid://shopify/ProductVariant/456",
    quantity: 2,
    acceptedUnitAmountMinor: 61000,
    currency: "USD",
  });

  assert.equal(input.presentmentCurrencyCode, "USD");
  assert.equal(input.acceptAutomaticDiscounts, false);
  assert.equal(input.allowDiscountCodesInCheckout, false);
  assert.deepEqual(input.lineItems, [
    {
      variantId: "gid://shopify/ProductVariant/456",
      quantity: 2,
      priceOverride: {
        amount: "610.00",
        currencyCode: "USD",
      },
    },
  ]);
  assert.deepEqual(input.customAttributes, [
    { key: "counterpilot_transaction_id", value: "cp_offer_123" },
  ]);
  assert.deepEqual(input.tags, ["counterpilot", "counterpilot-negotiated"]);
  assert.equal(input.visibleToCustomer, true);
});

test("createDraftOrderForAcceptedOffer calls draftOrderCreate and maps checkout fields", async () => {
  const fetchCalls = [];
  const fetchImpl = async (url, options) => {
    fetchCalls.push({ url, options });
    return Response.json({
      data: {
        draftOrderCreate: {
          draftOrder: {
            id: "gid://shopify/DraftOrder/123",
            invoiceUrl: "https://checkout.counterpilot.test/invoice/123",
          },
          userErrors: [],
        },
      },
    });
  };

  const result = await createDraftOrderForAcceptedOffer({
    shop: "counterpilot-dev.myshopify.com",
    adminAccessToken: "admin-token",
    apiVersion: "2026-04",
    transactionId: "cp_offer_123",
    variantRef: "gid://shopify/ProductVariant/456",
    quantity: 2,
    acceptedUnitAmountMinor: 61000,
    currency: "USD",
    productTitle: "The Complete Snowboard",
    fetchImpl,
  });

  assert.equal(result.draftOrderId, "gid://shopify/DraftOrder/123");
  assert.equal(
    result.checkoutUrl,
    "https://checkout.counterpilot.test/invoice/123",
  );
  assert.equal(
    fetchCalls[0].url,
    "https://counterpilot-dev.myshopify.com/admin/api/2026-04/graphql.json",
  );
  assert.equal(
    fetchCalls[0].options.headers["x-shopify-access-token"],
    "admin-token",
  );
  const body = JSON.parse(fetchCalls[0].options.body);
  assert.match(body.query, /draftOrderCreate/);
  assert.deepEqual(body.variables.input, result.input);
  assert.equal(
    body.variables.input.lineItems[0].priceOverride.amount,
    "610.00",
  );
  assert.equal(body.variables.input.acceptAutomaticDiscounts, false);
  assert.equal(body.variables.input.allowDiscountCodesInCheckout, false);
});

test("createDraftOrderForAcceptedOffer surfaces Shopify userErrors safely", async () => {
  const fetchImpl = async () =>
    Response.json({
      data: {
        draftOrderCreate: {
          draftOrder: null,
          userErrors: [
            { field: ["lineItems"], message: "Add at least 1 product" },
          ],
        },
      },
    });

  await assert.rejects(
    createDraftOrderForAcceptedOffer({
      shop: "counterpilot-dev.myshopify.com",
      adminAccessToken: "admin-token",
      apiVersion: "2026-04",
      transactionId: "cp_offer_123",
      variantRef: "gid://shopify/ProductVariant/456",
      quantity: 2,
      acceptedUnitAmountMinor: 61000,
      currency: "USD",
      fetchImpl,
    }),
    (error) => {
      assert.equal(error.code, "shopify_user_error");
      assert.equal(error.statusCode, 422);
      assert.deepEqual(error.safeUserErrors, [
        { field: "lineItems", message: "Add at least 1 product" },
      ]);
      assert.doesNotMatch(
        JSON.stringify(error),
        /gid:\/\/shopify\/ProductVariant/,
      );
      return true;
    },
  );
});
