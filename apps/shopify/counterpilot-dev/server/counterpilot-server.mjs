import crypto from "node:crypto";
import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createDraftOrderForAcceptedOffer } from "./shopify-draft-orders.mjs";
import { normalizeShopifyOrderWebhook } from "./shopify-order-webhooks.mjs";
import { normalizeShopifyRefundWebhook } from "./shopify-refund-webhooks.mjs";
import { normalizeShopifyReturnWebhook } from "./shopify-return-webhooks.mjs";

const DEFAULT_MAX_BODY_BYTES = 16 * 1024;
const DEFAULT_WEBHOOK_MAX_BODY_BYTES = 256 * 1024;
const DEFAULT_DATA_DIR = path.join(process.cwd(), ".counterpilot-data");
const DEFAULT_BUYER_RESPONSE_TTL_MS = 7 * 24 * 60 * 60 * 1000;

const OFFER_POST_PATHS = new Set([
  "/counterpilot/offers",
  "/apps/counterpilot/offers",
]);

const MERCHANT_INBOX_PATH = "/counterpilot/merchant/offers";
const SHOPIFY_ORDER_WEBHOOK_PATH = "/counterpilot/webhooks/shopify/orders";
const SHOPIFY_REFUND_WEBHOOK_PATH = "/counterpilot/webhooks/shopify/refunds";
const SHOPIFY_RETURN_WEBHOOK_PATH = "/counterpilot/webhooks/shopify/returns";

const ALLOWED_OFFER_FIELDS = new Set([
  "shop",
  "store_id",
  "product_ref",
  "product_gid",
  "variant_ref",
  "variant_gid",
  "product_title",
  "offer_amount",
  "currency",
  "quantity",
  "buyer_email",
  "buyer_contact_token",
]);

const MERCHANT_ACTION_FIELDS = {
  accept: new Set(["store_id"]),
  counter: new Set(["store_id", "counter_amount", "currency"]),
  decline: new Set(["store_id"]),
};

const MERCHANT_ACTION_EVENTS = {
  accept: "merchant_accepted",
  counter: "merchant_countered",
  decline: "merchant_declined",
};

const BUYER_RESPONSE_STATES = new Set([
  "merchant_accepted",
  "merchant_countered",
]);

const ORDER_WEBHOOK_READY_STATES = new Set([
  "checkout_created",
  "order_created",
  "paid",
]);

const REFUND_WEBHOOK_READY_STATES = new Set([
  "paid",
  "partially_refunded",
  "refunded",
  "mature",
]);

const RETURN_WEBHOOK_READY_STATES = new Set([
  "paid",
  "partially_refunded",
  "refunded",
  "mature",
]);

const FORBIDDEN_FIELDS = new Set([
  "address",
  "buyer_message",
  "checkout_url",
  "customer_email",
  "customer_name",
  "merchant_note",
  "message",
  "note",
  "notes",
  "phone",
  "raw_buyer_email",
  "refresh_token",
  "shipping_address",
  "token",
  "access_token",
]);

class OfferStore {
  constructor(dataDir) {
    this.dataDir = dataDir;
    this.filePath = path.join(dataDir, "offers.jsonl");
    this.checkoutRefsPath = path.join(dataDir, "checkout_refs.jsonl");
    this.orderRefsPath = path.join(dataDir, "order_refs.jsonl");
    this.refundRefsPath = path.join(dataDir, "refund_refs.jsonl");
    this.returnRefsPath = path.join(dataDir, "return_refs.jsonl");
    this.webhookDeliveriesPath = path.join(
      dataDir,
      "shopify_webhook_deliveries.jsonl",
    );
    this.writeQueue = Promise.resolve();
  }

  async append(record) {
    const operation = this.writeQueue.then(async () => {
      await this.#appendDirect(record);
      return record;
    });
    this.writeQueue = operation.then(
      () => undefined,
      () => undefined,
    );
    return operation;
  }

  async appendWithEvents(factory) {
    const operation = this.writeQueue.then(async () => {
      const events = await this.#readDirect();
      const record = factory(events);
      await this.#appendDirect(record);
      return { record, events: [...events, record] };
    });
    this.writeQueue = operation.then(
      () => undefined,
      () => undefined,
    );
    return operation;
  }

  async transaction(operationFactory) {
    const operation = this.writeQueue.then(async () => {
      const events = await this.#readDirect();
      const checkoutRefs = await this.#readCheckoutRefsDirect();
      const orderRefs = await this.#readOrderRefsDirect();
      const refundRefs = await this.#readRefundRefsDirect();
      const returnRefs = await this.#readReturnRefsDirect();
      const webhookDeliveries = await this.#readWebhookDeliveriesDirect();
      const appendEvent = async (record) => {
        await this.#appendDirect(record);
        events.push(record);
        return record;
      };
      const appendCheckoutRef = async (record) => {
        await this.#appendJsonlDirect(this.checkoutRefsPath, record);
        checkoutRefs.push(record);
        return record;
      };
      const appendOrderRef = async (record) => {
        await this.#appendJsonlDirect(this.orderRefsPath, record);
        orderRefs.push(record);
        return record;
      };
      const appendRefundRef = async (record) => {
        await this.#appendJsonlDirect(this.refundRefsPath, record);
        refundRefs.push(record);
        return record;
      };
      const appendReturnRef = async (record) => {
        await this.#appendJsonlDirect(this.returnRefsPath, record);
        returnRefs.push(record);
        return record;
      };
      const appendWebhookDelivery = async (record) => {
        await this.#appendJsonlDirect(this.webhookDeliveriesPath, record);
        webhookDeliveries.push(record);
        return record;
      };
      return operationFactory({
        events,
        checkoutRefs,
        orderRefs,
        refundRefs,
        returnRefs,
        webhookDeliveries,
        appendEvent,
        appendCheckoutRef,
        appendOrderRef,
        appendRefundRef,
        appendReturnRef,
        appendWebhookDelivery,
      });
    });
    this.writeQueue = operation.then(
      () => undefined,
      () => undefined,
    );
    return operation;
  }

  async list() {
    await this.writeQueue;
    return this.#readDirect();
  }

  async #appendDirect(record) {
    await this.#appendJsonlDirect(this.filePath, record);
  }

  async #appendJsonlDirect(filePath, record) {
    await fs.mkdir(this.dataDir, { recursive: true });
    await fs.appendFile(filePath, `${JSON.stringify(record)}\n`, "utf8");
  }

  async #readDirect() {
    return this.#readJsonlDirect(this.filePath);
  }

  async #readCheckoutRefsDirect() {
    return this.#readJsonlDirect(this.checkoutRefsPath);
  }

  async #readOrderRefsDirect() {
    return this.#readJsonlDirect(this.orderRefsPath);
  }

  async #readRefundRefsDirect() {
    return this.#readJsonlDirect(this.refundRefsPath);
  }

  async #readReturnRefsDirect() {
    return this.#readJsonlDirect(this.returnRefsPath);
  }

  async #readWebhookDeliveriesDirect() {
    return this.#readJsonlDirect(this.webhookDeliveriesPath);
  }

  async #readJsonlDirect(filePath) {
    try {
      const text = await fs.readFile(filePath, "utf8");
      return text
        .split(/\r?\n/)
        .filter(Boolean)
        .map((line) => JSON.parse(line));
    } catch (error) {
      if (error.code === "ENOENT") {
        return [];
      }
      throw error;
    }
  }
}

function hashValue(value) {
  return crypto
    .createHash("sha256")
    .update(String(value), "utf8")
    .digest("hex");
}

function createOpaqueToken() {
  return crypto.randomBytes(32).toString("base64url");
}

function hashToken(token) {
  return `sha256:${hashValue(token)}`;
}

function checkoutRefHash(value) {
  return `sha256:${hashValue(value)}`;
}

function shopifyAdminAccessToken(options) {
  return (
    options.shopifyAdminAccessToken ??
    process.env.COUNTERPILOT_SHOPIFY_ADMIN_ACCESS_TOKEN ??
    process.env.COUNTERPILOT_SHOPIFY_ACCESS_TOKEN ??
    process.env.SHOPIFY_ADMIN_ACCESS_TOKEN
  );
}

function shopifyApiVersion(options) {
  return (
    options.shopifyApiVersion ??
    process.env.COUNTERPILOT_SHOPIFY_API_VERSION ??
    "2026-04"
  );
}

function shopifyWebhookSecret(options) {
  return (
    options.shopifyWebhookSecret ??
    process.env.COUNTERPILOT_SHOPIFY_WEBHOOK_SECRET ??
    process.env.SHOPIFY_WEBHOOK_SECRET
  );
}

function shopifyProductionEvidence(options) {
  if (options.productionEvidence !== undefined) {
    return Boolean(options.productionEvidence);
  }
  return process.env.COUNTERPILOT_PRODUCTION_EVIDENCE === "1";
}

function buyerResponseTtlMs(options) {
  if (options.buyerResponseTtlMs !== undefined) {
    return options.buyerResponseTtlMs;
  }
  if (process.env.COUNTERPILOT_BUYER_RESPONSE_TTL_MS !== undefined) {
    const configured = Number(process.env.COUNTERPILOT_BUYER_RESPONSE_TTL_MS);
    return Number.isFinite(configured)
      ? configured
      : DEFAULT_BUYER_RESPONSE_TTL_MS;
  }
  return DEFAULT_BUYER_RESPONSE_TTL_MS;
}

function safeCompareHex(actual, expected) {
  if (!/^[a-f0-9]+$/i.test(actual) || !/^[a-f0-9]+$/i.test(expected)) {
    return false;
  }
  const actualBuffer = Buffer.from(actual, "hex");
  const expectedBuffer = Buffer.from(expected, "hex");
  if (actualBuffer.length !== expectedBuffer.length) {
    return false;
  }
  return crypto.timingSafeEqual(actualBuffer, expectedBuffer);
}

function safeCompareText(actual, expected) {
  const actualBuffer = Buffer.from(String(actual), "utf8");
  const expectedBuffer = Buffer.from(String(expected), "utf8");
  if (actualBuffer.length !== expectedBuffer.length) {
    return false;
  }
  return crypto.timingSafeEqual(actualBuffer, expectedBuffer);
}

export function calculateAppProxySignature(searchParams, secret) {
  const grouped = new Map();
  for (const [key, value] of searchParams) {
    if (key === "signature") {
      continue;
    }
    const values = grouped.get(key) ?? [];
    values.push(value);
    grouped.set(key, values);
  }
  const message = [...grouped.entries()]
    .map(([key, values]) => `${key}=${values.join(",")}`)
    .sort()
    .join("");
  return crypto
    .createHmac("sha256", secret)
    .update(message, "utf8")
    .digest("hex");
}

function verifyAppProxySignature(requestUrl, secret) {
  const signature = requestUrl.searchParams.get("signature");
  if (!signature) {
    throw validationError("app proxy signature is required", 401);
  }
  const calculated = calculateAppProxySignature(
    requestUrl.searchParams,
    secret,
  );
  if (!safeCompareHex(signature, calculated)) {
    throw validationError("app proxy signature is invalid", 401);
  }
}

function jsonResponse(response, statusCode, body) {
  const text = JSON.stringify(body);
  response.writeHead(statusCode, {
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Origin": "*",
    "Content-Length": Buffer.byteLength(text),
    "Content-Type": "application/json; charset=utf-8",
  });
  response.end(text);
}

function normalizeString(value, fieldName, maxLength = 255) {
  if (typeof value !== "string") {
    throw validationError(`${fieldName} must be a string`);
  }
  const normalized = value.trim();
  if (!normalized) {
    throw validationError(`${fieldName} is required`);
  }
  if (normalized.length > maxLength) {
    throw validationError(`${fieldName} is too long`);
  }
  return normalized;
}

function parsePositiveInteger(value, fieldName) {
  const normalized =
    typeof value === "number"
      ? String(value)
      : normalizeString(value, fieldName, 32);
  if (!/^[1-9]\d*$/.test(normalized)) {
    throw validationError(`${fieldName} must be a positive integer`);
  }
  const parsed = Number(normalized);
  if (!Number.isSafeInteger(parsed)) {
    throw validationError(`${fieldName} is too large`);
  }
  return parsed;
}

function parseMoneyMinor(value, fieldName) {
  const normalized =
    typeof value === "number"
      ? String(value)
      : normalizeString(value, fieldName, 32);
  if (!/^(0|[1-9]\d*)(\.\d{1,2})?$/.test(normalized)) {
    throw validationError(
      `${fieldName} must be a positive decimal with at most two cents digits`,
    );
  }
  const [units, cents = ""] = normalized.split(".");
  const amountMinor = Number(units) * 100 + Number(cents.padEnd(2, "0"));
  if (!Number.isSafeInteger(amountMinor) || amountMinor <= 0) {
    throw validationError(`${fieldName} must be greater than zero`);
  }
  return amountMinor;
}

function parseCurrency(value, fieldName = "currency") {
  const currency =
    value === undefined
      ? "USD"
      : normalizeString(value, fieldName, 3).toUpperCase();
  if (!/^[A-Z]{3}$/.test(currency)) {
    throw validationError(
      `${fieldName} must be a three-letter ISO currency code`,
    );
  }
  return currency;
}

function validationError(message, statusCode = 400) {
  const error = new Error(message);
  error.statusCode = statusCode;
  error.expose = true;
  return error;
}

function verifyMerchantAuth(request, options) {
  const merchantAuthToken =
    options.merchantAuthToken ?? process.env.COUNTERPILOT_MERCHANT_AUTH_TOKEN;
  if (!merchantAuthToken) {
    return;
  }
  const authorization = request.headers.authorization ?? "";
  const match = authorization.match(/^Bearer\s+(.+)$/);
  if (!match || !safeCompareText(match[1], merchantAuthToken)) {
    throw validationError("merchant authentication is required", 401);
  }
}

function validateAllowedFields(payload, allowedFields, surfaceName) {
  for (const key of Object.keys(payload)) {
    const lowerKey = key.toLowerCase();
    if (FORBIDDEN_FIELDS.has(lowerKey)) {
      throw validationError(
        `${key} is not accepted by the ${surfaceName} route`,
      );
    }
    if (!allowedFields.has(key)) {
      throw validationError(`${key} is not a supported ${surfaceName} field`);
    }
  }
}

function normalizeBuyerContact(payload) {
  const hasEmail =
    typeof payload.buyer_email === "string" &&
    payload.buyer_email.trim() !== "";
  const hasToken =
    typeof payload.buyer_contact_token === "string" &&
    payload.buyer_contact_token.trim() !== "";
  if (hasEmail && hasToken) {
    throw validationError(
      "provide buyer_email or buyer_contact_token, not both",
    );
  }
  if (!hasEmail && !hasToken) {
    throw validationError("buyer_email or buyer_contact_token is required");
  }
  if (hasEmail) {
    const email = normalizeString(
      payload.buyer_email,
      "buyer_email",
      320,
    ).toLowerCase();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      throw validationError("buyer_email must be a valid email address");
    }
    return {
      buyer_contact_hash: `sha256:${hashValue(email)}`,
      buyer_contact_reference: `email_hash:${hashValue(email).slice(0, 16)}`,
      buyer_contact_type: "email",
    };
  }
  const token = normalizeString(
    payload.buyer_contact_token,
    "buyer_contact_token",
    512,
  );
  return {
    buyer_contact_hash: `sha256:${hashValue(token)}`,
    buyer_contact_reference: `token_hash:${hashValue(token).slice(0, 16)}`,
    buyer_contact_type: "token",
  };
}

function resolveAppProxyStoreId(requestUrl, payload = {}, options = {}) {
  if (!requestUrl.pathname.startsWith("/apps/")) {
    return null;
  }

  const appProxySecret =
    options.appProxySecret ??
    process.env.COUNTERPILOT_SHOPIFY_API_SECRET ??
    process.env.SHOPIFY_API_SECRET;
  const requireAppProxySignature =
    options.requireAppProxySignature ??
    process.env.COUNTERPILOT_REQUIRE_APP_PROXY_SIGNATURE === "1";
  if (appProxySecret) {
    verifyAppProxySignature(requestUrl, appProxySecret);
  } else if (requireAppProxySignature) {
    throw validationError(
      "app proxy signature verification is not configured",
      401,
    );
  }

  const shop = normalizeString(requestUrl.searchParams.get("shop"), "shop");
  const bodyStoreId = payload.store_id ?? payload.shop;
  if (
    bodyStoreId !== undefined &&
    normalizeString(bodyStoreId, "store_id") !== shop
  ) {
    throw validationError(
      "request shop does not match the signed app proxy shop",
      403,
    );
  }
  return shop;
}

export function normalizeOfferPayload(payload, now = new Date(), options = {}) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw validationError("request body must be a JSON object");
  }
  validateAllowedFields(payload, ALLOWED_OFFER_FIELDS, "offer intake");

  const storeId =
    options.trustedStoreId ??
    normalizeString(payload.store_id ?? payload.shop, "store_id");
  const productRef = normalizeString(
    payload.product_ref ?? payload.product_gid,
    "product_ref",
    512,
  );
  const variantRef = payload.variant_ref ?? payload.variant_gid;
  const productTitle =
    payload.product_title === undefined
      ? null
      : normalizeString(payload.product_title, "product_title", 255);
  const currency = parseCurrency(payload.currency);
  const buyerContact = normalizeBuyerContact(payload);
  const occurredAt = now.toISOString();

  return {
    schema_version: "counterpilot.offer_event.v1",
    transaction_id: `cp_offer_${crypto.randomUUID()}`,
    lifecycle_state: "offer_submitted",
    event_type: "offer_submitted",
    actor_type: "buyer",
    occurred_at: occurredAt,
    received_at: occurredAt,
    source: "counterpilot_theme_block",
    store_id: storeId,
    store_reference_hash: `sha256:${hashValue(storeId)}`,
    product_title: productTitle,
    product_reference_hash: `sha256:${hashValue(productRef)}`,
    variant_reference_hash: variantRef
      ? `sha256:${hashValue(normalizeString(variantRef, "variant_ref", 512))}`
      : null,
    offer_amount_minor: parseMoneyMinor(payload.offer_amount, "offer_amount"),
    currency,
    quantity: parsePositiveInteger(payload.quantity ?? 1, "quantity"),
    buyer_contact_hash: buyerContact.buyer_contact_hash,
    buyer_contact_reference: buyerContact.buyer_contact_reference,
    buyer_contact_type: buyerContact.buyer_contact_type,
    operational_refs: {
      product_ref: productRef,
      variant_ref: variantRef
        ? normalizeString(variantRef, "variant_ref", 512)
        : null,
    },
  };
}

function normalizeMerchantPayload(payload, action) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw validationError("request body must be a JSON object");
  }
  const allowedFields = MERCHANT_ACTION_FIELDS[action];
  validateAllowedFields(payload, allowedFields, "merchant action");
  return {
    store_id: normalizeString(payload.store_id, "store_id"),
    counter_amount_minor:
      action === "counter"
        ? parseMoneyMinor(payload.counter_amount, "counter_amount")
        : null,
    currency: action === "counter" ? parseCurrency(payload.currency) : null,
  };
}

function clearMaturityFields(snapshot) {
  delete snapshot.matured_at;
  delete snapshot.maturity_window_days;
  delete snapshot.refund_total_minor;
  delete snapshot.net_revenue_minor;
  delete snapshot.product_cost_minor;
  delete snapshot.maturity_shipping_cost_minor;
  delete snapshot.platform_fee_minor;
  delete snapshot.return_loss_minor;
  delete snapshot.mature_margin_minor;
  delete snapshot.mature_currency;
  delete snapshot.margin_config_source;
  delete snapshot.maturity_input_hash;
}

export function buildOfferSnapshots(events) {
  const snapshots = new Map();
  for (const event of events) {
    if (event.event_type === "offer_submitted") {
      snapshots.set(event.transaction_id, {
        transaction_id: event.transaction_id,
        lifecycle_state: event.lifecycle_state,
        event_type: event.event_type,
        submitted_at: event.occurred_at,
        updated_at: event.occurred_at,
        store_id: event.store_id,
        product_title: event.product_title,
        product_reference_hash: event.product_reference_hash,
        variant_reference_hash: event.variant_reference_hash,
        offer_amount_minor: event.offer_amount_minor,
        currency: event.currency,
        quantity: event.quantity,
        buyer_contact_reference: event.buyer_contact_reference,
        operational_refs: event.operational_refs,
      });
      continue;
    }

    const snapshot = snapshots.get(event.transaction_id);
    if (!snapshot) {
      continue;
    }
    snapshot.lifecycle_state = event.lifecycle_state;
    snapshot.event_type = event.event_type;
    snapshot.updated_at = event.occurred_at;
    snapshot.merchant_action_at = event.occurred_at;
    if (event.event_type === "merchant_countered") {
      snapshot.counter_amount_minor = event.counter_amount_minor;
      snapshot.counter_currency = event.currency;
    }
    if (BUYER_RESPONSE_STATES.has(event.event_type)) {
      snapshot.buyer_response_token_hash = event.buyer_response_token_hash;
      snapshot.buyer_response_expires_at = event.buyer_response_expires_at;
      snapshot.acceptable_amount_minor =
        event.event_type === "merchant_countered"
          ? event.counter_amount_minor
          : snapshot.offer_amount_minor;
      snapshot.acceptable_currency =
        event.event_type === "merchant_countered"
          ? event.currency
          : snapshot.currency;
      snapshot.accepted_from_event_type = event.event_type;
    }
    if (event.event_type === "buyer_accepted") {
      snapshot.accepted_amount_minor = event.accepted_amount_minor;
      snapshot.accepted_currency = event.currency;
      snapshot.accepted_from_event_type = event.accepted_from_event_type;
      snapshot.buyer_accepted_at = event.occurred_at;
    }
    if (event.event_type === "checkout_creation_started") {
      snapshot.lifecycle_state = event.lifecycle_state;
      snapshot.event_type = event.event_type;
      snapshot.updated_at = event.occurred_at;
      snapshot.checkout_creation_started_at = event.occurred_at;
      snapshot.checkout_creation_status = "started";
    }
    if (event.event_type === "checkout_created") {
      snapshot.lifecycle_state = event.lifecycle_state;
      snapshot.event_type = event.event_type;
      snapshot.updated_at = event.occurred_at;
      snapshot.checkout_created_at = event.occurred_at;
      snapshot.checkout_creation_status = "created";
      snapshot.accepted_amount_minor = event.accepted_amount_minor;
      snapshot.accepted_currency = event.currency;
      snapshot.negotiated_revenue_minor = event.negotiated_revenue_minor;
      snapshot.draft_order_reference_hash = event.draft_order_reference_hash;
      snapshot.checkout_reference_hash = event.checkout_reference_hash;
    }
    if (event.event_type === "checkout_creation_failed") {
      snapshot.lifecycle_state = event.lifecycle_state;
      snapshot.event_type = event.event_type;
      snapshot.updated_at = event.occurred_at;
      snapshot.checkout_creation_failed_at = event.occurred_at;
      snapshot.checkout_error_code = event.error_code;
      snapshot.checkout_creation_status = "failed";
    }
    if (event.event_type === "order_created") {
      snapshot.lifecycle_state = event.lifecycle_state;
      snapshot.event_type = event.event_type;
      snapshot.updated_at = event.occurred_at;
      snapshot.order_created_at = event.occurred_at;
      snapshot.order_reference_hash = event.order_reference_hash;
      snapshot.order_name_reference_hash = event.order_name_reference_hash;
      snapshot.order_total_minor = event.order_total_minor;
      snapshot.shipping_total_minor = event.shipping_total_minor;
      snapshot.tax_total_minor = event.tax_total_minor;
      snapshot.discount_total_minor = event.discount_total_minor;
      snapshot.order_currency = event.currency;
      snapshot.production_evidence = event.production_evidence;
    }
    if (event.event_type === "paid") {
      snapshot.lifecycle_state = event.lifecycle_state;
      snapshot.payment_lifecycle_state = event.lifecycle_state;
      snapshot.event_type = event.event_type;
      snapshot.updated_at = event.occurred_at;
      snapshot.paid_at = event.paid_at;
      snapshot.order_reference_hash = event.order_reference_hash;
      snapshot.paid_total_minor = event.paid_total_minor;
      snapshot.paid_currency = event.currency;
      snapshot.production_evidence = event.production_evidence;
    }
    if (event.event_type === "refund_recorded") {
      if (snapshot.lifecycle_state === "mature") {
        clearMaturityFields(snapshot);
      }
      snapshot.lifecycle_state = event.lifecycle_state;
      snapshot.payment_lifecycle_state = event.lifecycle_state;
      snapshot.event_type = event.event_type;
      snapshot.updated_at = event.occurred_at;
      snapshot.refund_recorded_at = event.occurred_at;
      snapshot.processed_at = event.processed_at;
      snapshot.refund_reference_hash = event.refund_reference_hash;
      snapshot.order_reference_hash = event.order_reference_hash;
      snapshot.latest_refund_total_minor = event.refund_total_minor;
      snapshot.cumulative_refund_total_minor =
        event.cumulative_refund_total_minor;
      snapshot.refund_amount_source = event.refund_amount_source;
      snapshot.refund_currency = event.currency;
      snapshot.production_evidence = event.production_evidence;
    }
    if (event.event_type === "return_status_recorded") {
      snapshot.updated_at = event.occurred_at;
      snapshot.return_status_recorded_at = event.occurred_at;
      snapshot.return_reference_hash = event.return_reference_hash;
      snapshot.order_reference_hash = event.order_reference_hash;
      snapshot.return_status = event.return_status;
      snapshot.return_exposure_state = event.return_exposure_state;
      snapshot.total_return_line_items = event.total_return_line_items;
      snapshot.return_production_evidence = event.production_evidence;
      if (snapshot.lifecycle_state === "mature") {
        clearMaturityFields(snapshot);
        snapshot.lifecycle_state = snapshot.payment_lifecycle_state ?? "paid";
        snapshot.event_type = event.event_type;
      }
    }
    if (event.event_type === "mature") {
      snapshot.lifecycle_state = event.lifecycle_state;
      snapshot.payment_lifecycle_state =
        event.payment_lifecycle_state ??
        snapshot.payment_lifecycle_state ??
        "paid";
      snapshot.event_type = event.event_type;
      snapshot.updated_at = event.occurred_at;
      snapshot.matured_at = event.matured_at;
      snapshot.maturity_window_days = event.maturity_window_days;
      snapshot.refund_total_minor = event.refund_total_minor;
      snapshot.net_revenue_minor = event.net_revenue_minor;
      snapshot.product_cost_minor = event.product_cost_minor;
      snapshot.maturity_shipping_cost_minor = event.shipping_cost_minor;
      snapshot.platform_fee_minor = event.platform_fee_minor;
      snapshot.return_loss_minor = event.return_loss_minor;
      snapshot.mature_margin_minor = event.mature_margin_minor;
      snapshot.mature_currency = event.currency;
      snapshot.margin_config_source = event.margin_config_source;
      snapshot.maturity_input_hash = event.maturity_input_hash;
      snapshot.return_exposure_state = event.return_exposure_state;
      snapshot.production_evidence = event.production_evidence;
    }
  }
  return snapshots;
}

export function sanitizeOfferForInbox(record) {
  const sanitized = {
    transaction_id: record.transaction_id,
    lifecycle_state: record.lifecycle_state,
    event_type: record.event_type,
    submitted_at: record.submitted_at,
    updated_at: record.updated_at,
    merchant_action_at: record.merchant_action_at,
    store_id: record.store_id,
    product_title: record.product_title,
    product_reference_hash: record.product_reference_hash,
    variant_reference_hash: record.variant_reference_hash,
    offer_amount_minor: record.offer_amount_minor,
    counter_amount_minor: record.counter_amount_minor,
    counter_currency: record.counter_currency,
    accepted_amount_minor: record.accepted_amount_minor,
    accepted_currency: record.accepted_currency,
    buyer_accepted_at: record.buyer_accepted_at,
    checkout_creation_started_at: record.checkout_creation_started_at,
    checkout_creation_status: record.checkout_creation_status,
    checkout_created_at: record.checkout_created_at,
    checkout_creation_failed_at: record.checkout_creation_failed_at,
    checkout_error_code: record.checkout_error_code,
    negotiated_revenue_minor: record.negotiated_revenue_minor,
    draft_order_reference_hash: record.draft_order_reference_hash,
    checkout_reference_hash: record.checkout_reference_hash,
    order_created_at: record.order_created_at,
    order_reference_hash: record.order_reference_hash,
    order_name_reference_hash: record.order_name_reference_hash,
    order_total_minor: record.order_total_minor,
    shipping_total_minor: record.shipping_total_minor,
    tax_total_minor: record.tax_total_minor,
    discount_total_minor: record.discount_total_minor,
    order_currency: record.order_currency,
    paid_at: record.paid_at,
    paid_total_minor: record.paid_total_minor,
    paid_currency: record.paid_currency,
    refund_recorded_at: record.refund_recorded_at,
    processed_at: record.processed_at,
    refund_reference_hash: record.refund_reference_hash,
    latest_refund_total_minor: record.latest_refund_total_minor,
    cumulative_refund_total_minor: record.cumulative_refund_total_minor,
    refund_amount_source: record.refund_amount_source,
    refund_currency: record.refund_currency,
    return_status_recorded_at: record.return_status_recorded_at,
    return_reference_hash: record.return_reference_hash,
    return_status: record.return_status,
    return_exposure_state: record.return_exposure_state,
    total_return_line_items: record.total_return_line_items,
    return_production_evidence: record.return_production_evidence,
    production_evidence: record.production_evidence,
    currency: record.currency,
    quantity: record.quantity,
    buyer_contact_reference: record.buyer_contact_reference,
  };
  if (record.lifecycle_state === "mature") {
    Object.assign(sanitized, {
      matured_at: record.matured_at,
      maturity_window_days: record.maturity_window_days,
      refund_total_minor: record.refund_total_minor,
      net_revenue_minor: record.net_revenue_minor,
      product_cost_minor: record.product_cost_minor,
      maturity_shipping_cost_minor: record.maturity_shipping_cost_minor,
      platform_fee_minor: record.platform_fee_minor,
      return_loss_minor: record.return_loss_minor,
      mature_margin_minor: record.mature_margin_minor,
      mature_currency: record.mature_currency,
      margin_config_source: record.margin_config_source,
      maturity_input_hash: record.maturity_input_hash,
    });
  }
  return sanitized;
}

function getSnapshotOrThrow(events, transactionId, storeId) {
  const snapshots = buildOfferSnapshots(events);
  const snapshot = snapshots.get(transactionId);
  if (!snapshot) {
    throw validationError("offer transaction was not found", 404);
  }
  if (snapshot.store_id !== storeId) {
    throw validationError(
      "offer transaction does not belong to this store",
      403,
    );
  }
  return snapshot;
}

function createMerchantActionEvent(
  snapshot,
  action,
  payload,
  options,
  now = new Date(),
) {
  if (snapshot.lifecycle_state !== "offer_submitted") {
    throw validationError(
      `cannot ${action} an offer in ${snapshot.lifecycle_state} state`,
      409,
    );
  }
  if (action === "counter" && payload.currency !== snapshot.currency) {
    throw validationError(
      "counter currency must match the original offer currency",
    );
  }

  const eventType = MERCHANT_ACTION_EVENTS[action];
  const event = {
    schema_version: "counterpilot.offer_event.v1",
    transaction_id: snapshot.transaction_id,
    lifecycle_state: eventType,
    event_type: eventType,
    actor_type: "merchant",
    occurred_at: now.toISOString(),
    store_id: snapshot.store_id,
    store_reference_hash: `sha256:${hashValue(snapshot.store_id)}`,
    source: "counterpilot_merchant_surface",
  };
  if (action === "counter") {
    event.counter_amount_minor = payload.counter_amount_minor;
    event.currency = payload.currency;
  }
  let buyerResponsePath = null;
  if (action === "accept" || action === "counter") {
    const buyerResponseToken = createOpaqueToken();
    event.buyer_response_token_hash = hashToken(buyerResponseToken);
    event.buyer_response_expires_at = new Date(
      now.getTime() + buyerResponseTtlMs(options),
    ).toISOString();
    buyerResponsePath = `/apps/counterpilot/offers/${encodeURIComponent(
      snapshot.transaction_id,
    )}/respond?token=${encodeURIComponent(buyerResponseToken)}`;
  }
  return { event, buyerResponsePath };
}

function sanitizeBuyerResponseView(snapshot) {
  return {
    transaction_id: snapshot.transaction_id,
    status: snapshot.lifecycle_state,
    product_title: snapshot.product_title,
    original_offer_amount_minor: snapshot.offer_amount_minor,
    accepted_amount_minor: snapshot.acceptable_amount_minor,
    currency: snapshot.acceptable_currency,
    quantity: snapshot.quantity,
    expires_at: snapshot.buyer_response_expires_at,
  };
}

function validateBuyerResponseSnapshot(snapshot, token, now = new Date()) {
  if (
    !BUYER_RESPONSE_STATES.has(snapshot.lifecycle_state) &&
    snapshot.lifecycle_state !== "buyer_accepted" &&
    snapshot.lifecycle_state !== "checkout_created"
  ) {
    throw validationError(
      `cannot accept an offer in ${snapshot.lifecycle_state} state`,
      409,
    );
  }
  if (!snapshot.buyer_response_token_hash) {
    throw validationError("buyer response token is not available", 401);
  }
  if (typeof token !== "string" || token.trim() === "") {
    throw validationError("buyer response token is required", 401);
  }
  const tokenHash = hashToken(normalizeString(token, "token", 512));
  if (!safeCompareText(tokenHash, snapshot.buyer_response_token_hash)) {
    throw validationError("buyer response token is invalid", 401);
  }
  if (
    snapshot.buyer_response_expires_at &&
    new Date(snapshot.buyer_response_expires_at).getTime() <= now.getTime()
  ) {
    throw validationError("buyer response token has expired", 410);
  }
}

function createBuyerAcceptedEvent(snapshot, now = new Date()) {
  return {
    schema_version: "counterpilot.offer_event.v1",
    transaction_id: snapshot.transaction_id,
    lifecycle_state: "buyer_accepted",
    event_type: "buyer_accepted",
    actor_type: "buyer",
    occurred_at: now.toISOString(),
    store_id: snapshot.store_id,
    store_reference_hash: `sha256:${hashValue(snapshot.store_id)}`,
    source: "counterpilot_buyer_response",
    accepted_amount_minor: snapshot.acceptable_amount_minor,
    currency: snapshot.acceptable_currency,
    accepted_from_event_type: snapshot.accepted_from_event_type,
  };
}

function checkoutRequestHash(snapshot) {
  return checkoutRefHash(
    [
      snapshot.transaction_id,
      snapshot.accepted_amount_minor,
      snapshot.accepted_currency,
      snapshot.quantity,
    ].join(":"),
  );
}

function createCheckoutCreationStartedEvent(snapshot, now = new Date()) {
  return {
    schema_version: "counterpilot.offer_event.v1",
    transaction_id: snapshot.transaction_id,
    lifecycle_state: "buyer_accepted",
    event_type: "checkout_creation_started",
    actor_type: "system",
    occurred_at: now.toISOString(),
    store_id: snapshot.store_id,
    store_reference_hash: `sha256:${hashValue(snapshot.store_id)}`,
    source: "counterpilot_shopify_draft_order",
    accepted_amount_minor: snapshot.accepted_amount_minor,
    currency: snapshot.accepted_currency,
    quantity: snapshot.quantity,
    checkout_request_reference_hash: checkoutRequestHash(snapshot),
  };
}

function createCheckoutCreatedEvent(snapshot, draftOrder, now = new Date()) {
  return {
    schema_version: "counterpilot.offer_event.v1",
    transaction_id: snapshot.transaction_id,
    lifecycle_state: "checkout_created",
    event_type: "checkout_created",
    actor_type: "system",
    occurred_at: now.toISOString(),
    store_id: snapshot.store_id,
    store_reference_hash: `sha256:${hashValue(snapshot.store_id)}`,
    source: "counterpilot_shopify_draft_order",
    accepted_amount_minor: snapshot.accepted_amount_minor,
    currency: snapshot.accepted_currency,
    quantity: snapshot.quantity,
    negotiated_revenue_minor:
      snapshot.accepted_amount_minor * snapshot.quantity,
    draft_order_reference_hash: checkoutRefHash(draftOrder.draftOrderId),
    checkout_reference_hash: checkoutRefHash(draftOrder.checkoutUrl),
  };
}

function createCheckoutRefRecord(snapshot, draftOrder, now = new Date()) {
  return {
    schema_version: "counterpilot.checkout_ref.v1",
    transaction_id: snapshot.transaction_id,
    store_id: snapshot.store_id,
    created_at: now.toISOString(),
    draft_order_id: draftOrder.draftOrderId,
    checkout_url: draftOrder.checkoutUrl,
    draft_order_reference_hash: checkoutRefHash(draftOrder.draftOrderId),
    checkout_reference_hash: checkoutRefHash(draftOrder.checkoutUrl),
  };
}

function checkoutFailureDetails(error) {
  return {
    error_code: error.code ?? "shopify_checkout_creation_failed",
    statusCode: error.statusCode ?? 502,
    user_errors: Array.isArray(error.safeUserErrors)
      ? error.safeUserErrors
      : [],
  };
}

function createCheckoutCreationFailedEvent(
  snapshot,
  failure,
  now = new Date(),
) {
  return {
    schema_version: "counterpilot.offer_event.v1",
    transaction_id: snapshot.transaction_id,
    lifecycle_state: "buyer_accepted",
    event_type: "checkout_creation_failed",
    actor_type: "system",
    occurred_at: now.toISOString(),
    store_id: snapshot.store_id,
    store_reference_hash: `sha256:${hashValue(snapshot.store_id)}`,
    source: "counterpilot_shopify_draft_order",
    accepted_amount_minor: snapshot.accepted_amount_minor,
    currency: snapshot.accepted_currency,
    quantity: snapshot.quantity,
    error_code: failure.error_code,
    user_errors: failure.user_errors,
  };
}

function latestCheckoutRefFor(checkoutRefs, transactionId) {
  return [...checkoutRefs]
    .reverse()
    .find((record) => record.transaction_id === transactionId);
}

function checkoutResponse(snapshot, checkoutRef) {
  const body = {
    offer: sanitizeOfferForInbox(snapshot),
    checkout_created: snapshot.lifecycle_state === "checkout_created",
  };
  if (checkoutRef?.checkout_url) {
    body.checkout_url = checkoutRef.checkout_url;
  }
  return body;
}

function checkoutAdapter(options) {
  return options.shopifyDraftOrderAdapter ?? createDraftOrderForAcceptedOffer;
}

function hasTransactionEvent(events, transactionId, eventType) {
  return events.some(
    (event) =>
      event.transaction_id === transactionId && event.event_type === eventType,
  );
}

function latestWebhookDeliveryFor(webhookDeliveries, deliveryId) {
  return [...webhookDeliveries]
    .reverse()
    .find((record) => record.delivery_id === deliveryId);
}

function hasOrderRef(orderRefs, transactionId, orderReferenceHash) {
  return orderRefs.some(
    (record) =>
      record.transaction_id === transactionId &&
      record.order_reference_hash === orderReferenceHash,
  );
}

function findOrderRefByHash(orderRefs, orderReferenceHash) {
  return [...orderRefs]
    .reverse()
    .find((record) => record.order_reference_hash === orderReferenceHash);
}

function hasProcessedRefundRef(refundRefs, transactionId, refundReferenceHash) {
  return refundRefs.some(
    (record) =>
      record.transaction_id === transactionId &&
      record.refund_reference_hash === refundReferenceHash &&
      record.status === "processed",
  );
}

function latestProcessedReturnStatusRef(
  returnRefs,
  transactionId,
  returnReferenceHash,
) {
  return [...returnRefs]
    .reverse()
    .find(
      (record) =>
        record.transaction_id === transactionId &&
        record.return_reference_hash === returnReferenceHash &&
        record.status === "processed",
    );
}

function nullableReferenceHash(value) {
  return value === null || value === undefined ? null : checkoutRefHash(value);
}

function createOrderCreatedEvent(snapshot, webhook) {
  return {
    schema_version: "counterpilot.offer_event.v1",
    transaction_id: snapshot.transaction_id,
    lifecycle_state: "order_created",
    event_type: "order_created",
    actor_type: "shopify",
    occurred_at: webhook.occurredAt,
    store_id: snapshot.store_id,
    store_reference_hash: `sha256:${hashValue(snapshot.store_id)}`,
    source: webhook.source,
    order_reference_hash: checkoutRefHash(webhook.order.reference),
    order_name_reference_hash: nullableReferenceHash(
      webhook.order.nameReference,
    ),
    currency: webhook.order.currency,
    order_total_minor: webhook.order.orderTotalMinor,
    shipping_total_minor: webhook.order.shippingTotalMinor,
    tax_total_minor: webhook.order.taxTotalMinor,
    discount_total_minor: webhook.order.discountTotalMinor,
    production_evidence: webhook.productionEvidence,
  };
}

function createPaidEvent(snapshot, webhook) {
  return {
    schema_version: "counterpilot.offer_event.v1",
    transaction_id: snapshot.transaction_id,
    lifecycle_state: "paid",
    event_type: "paid",
    actor_type: "shopify",
    occurred_at: webhook.paidAt,
    paid_at: webhook.paidAt,
    store_id: snapshot.store_id,
    store_reference_hash: `sha256:${hashValue(snapshot.store_id)}`,
    source: webhook.source,
    order_reference_hash: checkoutRefHash(webhook.order.reference),
    paid_total_minor: webhook.order.orderTotalMinor,
    currency: webhook.order.currency,
    production_evidence: webhook.productionEvidence,
  };
}

function createOrderRefRecord(snapshot, webhook, now = new Date()) {
  return {
    schema_version: "counterpilot.order_ref.v1",
    transaction_id: snapshot.transaction_id,
    store_id: snapshot.store_id,
    created_at: now.toISOString(),
    delivery_id: webhook.deliveryId,
    topic: webhook.topic,
    order_id: webhook.order.rawOrderId,
    admin_graphql_api_id: webhook.order.adminGraphqlApiId,
    order_name: webhook.order.name,
    order_reference_hash: checkoutRefHash(webhook.order.reference),
    order_name_reference_hash: nullableReferenceHash(
      webhook.order.nameReference,
    ),
  };
}

function createWebhookDeliveryRecord(webhook, status, now = new Date()) {
  return {
    schema_version: "counterpilot.shopify_webhook_delivery.v1",
    delivery_id: webhook.deliveryId,
    topic: webhook.topic,
    store_id: webhook.shop,
    transaction_id: webhook.transactionId,
    status,
    received_at: now.toISOString(),
    order_reference_hash: checkoutRefHash(webhook.order.reference),
  };
}

function createRefundRefRecord({
  refundWebhook,
  transactionId = null,
  orderReferenceHash = null,
  status,
  now = new Date(),
}) {
  return {
    schema_version: "counterpilot.refund_ref.v1",
    transaction_id: transactionId,
    store_id: refundWebhook.shop,
    created_at: now.toISOString(),
    delivery_id: refundWebhook.deliveryId,
    topic: refundWebhook.topic,
    status,
    reconciliation_reason: refundWebhook.reconciliationReason ?? null,
    order_id: refundWebhook.order?.rawOrderId ?? null,
    refund_id: refundWebhook.refund?.rawRefundId ?? null,
    refund_admin_graphql_api_id:
      refundWebhook.refund?.adminGraphqlApiId ?? null,
    refund_transaction_refs: refundWebhook.refund?.transactionRefs ?? [],
    order_reference_hash:
      orderReferenceHash ??
      (refundWebhook.order?.reference
        ? checkoutRefHash(refundWebhook.order.reference)
        : null),
    refund_reference_hash: refundWebhook.refund?.reference
      ? checkoutRefHash(refundWebhook.refund.reference)
      : null,
  };
}

function createRefundWebhookDeliveryRecord(
  refundWebhook,
  status,
  transactionId = null,
  now = new Date(),
) {
  return {
    schema_version: "counterpilot.shopify_webhook_delivery.v1",
    delivery_id: refundWebhook.deliveryId,
    topic: refundWebhook.topic,
    store_id: refundWebhook.shop,
    transaction_id: transactionId,
    status,
    received_at: now.toISOString(),
    order_reference_hash: refundWebhook.order?.reference
      ? checkoutRefHash(refundWebhook.order.reference)
      : null,
    refund_reference_hash: refundWebhook.refund?.reference
      ? checkoutRefHash(refundWebhook.refund.reference)
      : null,
    reconciliation_reason: refundWebhook.reconciliationReason ?? null,
  };
}

function createReturnRefRecord({
  returnWebhook,
  transactionId = null,
  orderReferenceHash = null,
  status,
  now = new Date(),
}) {
  return {
    schema_version: "counterpilot.return_ref.v1",
    transaction_id: transactionId,
    store_id: returnWebhook.shop,
    created_at: now.toISOString(),
    delivery_id: returnWebhook.deliveryId,
    topic: returnWebhook.topic,
    status,
    order_id: returnWebhook.order?.rawOrderId ?? null,
    order_admin_graphql_api_id: returnWebhook.order?.adminGraphqlApiId ?? null,
    return_id: returnWebhook.return?.rawReturnId ?? null,
    return_admin_graphql_api_id:
      returnWebhook.return?.adminGraphqlApiId ?? null,
    return_name: returnWebhook.return?.name ?? null,
    return_status: returnWebhook.return?.status ?? null,
    return_exposure_state: returnWebhook.return?.exposureState ?? null,
    total_return_line_items: returnWebhook.return?.totalReturnLineItems ?? null,
    order_reference_hash:
      orderReferenceHash ??
      (returnWebhook.order?.reference
        ? checkoutRefHash(returnWebhook.order.reference)
        : null),
    return_reference_hash: returnWebhook.return?.reference
      ? checkoutRefHash(returnWebhook.return.reference)
      : null,
  };
}

function createReturnWebhookDeliveryRecord(
  returnWebhook,
  status,
  transactionId = null,
  now = new Date(),
) {
  return {
    schema_version: "counterpilot.shopify_webhook_delivery.v1",
    delivery_id: returnWebhook.deliveryId,
    topic: returnWebhook.topic,
    store_id: returnWebhook.shop,
    transaction_id: transactionId,
    status,
    received_at: now.toISOString(),
    order_reference_hash: returnWebhook.order?.reference
      ? checkoutRefHash(returnWebhook.order.reference)
      : null,
    return_reference_hash: returnWebhook.return?.reference
      ? checkoutRefHash(returnWebhook.return.reference)
      : null,
    return_status: returnWebhook.return?.status ?? null,
    return_exposure_state: returnWebhook.return?.exposureState ?? null,
  };
}

function priorRefundTotalMinor(events, transactionId) {
  return events
    .filter(
      (event) =>
        event.transaction_id === transactionId &&
        event.event_type === "refund_recorded",
    )
    .reduce((total, event) => total + event.refund_total_minor, 0);
}

function createRefundRecordedEvent({
  snapshot,
  refundWebhook,
  refundReferenceHash,
  orderReferenceHash,
  cumulativeRefundTotalMinor,
}) {
  const lifecycleState =
    cumulativeRefundTotalMinor >= snapshot.paid_total_minor
      ? "refunded"
      : "partially_refunded";
  return {
    schema_version: "counterpilot.offer_event.v1",
    transaction_id: snapshot.transaction_id,
    lifecycle_state: lifecycleState,
    event_type: "refund_recorded",
    actor_type: "shopify",
    source: "shopify_refunds_create_webhook",
    occurred_at: refundWebhook.processedAt,
    processed_at: refundWebhook.processedAt,
    store_id: snapshot.store_id,
    store_reference_hash: `sha256:${hashValue(snapshot.store_id)}`,
    order_reference_hash: orderReferenceHash,
    refund_reference_hash: refundReferenceHash,
    refund_total_minor: refundWebhook.refund.refundTotalMinor,
    cumulative_refund_total_minor: cumulativeRefundTotalMinor,
    currency: refundWebhook.refund.currency,
    refund_amount_source: refundWebhook.refund.amountSource,
    production_evidence: refundWebhook.productionEvidence,
  };
}

function createReturnStatusRecordedEvent({
  snapshot,
  returnWebhook,
  returnReferenceHash,
  orderReferenceHash,
}) {
  return {
    schema_version: "counterpilot.offer_event.v1",
    transaction_id: snapshot.transaction_id,
    lifecycle_state: snapshot.lifecycle_state,
    event_type: "return_status_recorded",
    actor_type: "shopify",
    source: returnWebhook.source,
    occurred_at: returnWebhook.occurredAt,
    store_id: snapshot.store_id,
    store_reference_hash: `sha256:${hashValue(snapshot.store_id)}`,
    order_reference_hash: orderReferenceHash,
    return_reference_hash: returnReferenceHash,
    return_status: returnWebhook.return.status,
    return_exposure_state: returnWebhook.return.exposureState,
    total_return_line_items: returnWebhook.return.totalReturnLineItems,
    production_evidence: returnWebhook.productionEvidence,
  };
}

async function readJsonRequest(request, maxBodyBytes) {
  const chunks = [];
  let totalBytes = 0;
  for await (const chunk of request) {
    totalBytes += chunk.length;
    if (totalBytes > maxBodyBytes) {
      throw validationError("request body is too large", 413);
    }
    chunks.push(chunk);
  }
  const text = Buffer.concat(chunks).toString("utf8");
  if (!text.trim()) {
    throw validationError("request body is required");
  }
  try {
    return JSON.parse(text);
  } catch {
    throw validationError("request body must be valid JSON");
  }
}

async function readRawRequest(request, maxBodyBytes) {
  const chunks = [];
  let totalBytes = 0;
  for await (const chunk of request) {
    totalBytes += chunk.length;
    if (totalBytes > maxBodyBytes) {
      throw validationError("request body is too large", 413);
    }
    chunks.push(chunk);
  }
  const rawBody = Buffer.concat(chunks);
  if (rawBody.length === 0) {
    throw validationError("request body is required");
  }
  return rawBody;
}

async function handleOfferPost(
  request,
  requestUrl,
  response,
  store,
  maxBodyBytes,
  options,
) {
  const payload = await readJsonRequest(request, maxBodyBytes);
  const trustedStoreId = resolveAppProxyStoreId(requestUrl, payload, options);
  const record = normalizeOfferPayload(payload, new Date(), { trustedStoreId });
  await store.append(record);
  jsonResponse(response, 201, {
    received: true,
    transaction_id: record.transaction_id,
    lifecycle_state: record.lifecycle_state,
    offer_amount_minor: record.offer_amount_minor,
    currency: record.currency,
    quantity: record.quantity,
    product_title: record.product_title,
  });
}

async function handleInboxGet(request, requestUrl, response, store, options) {
  verifyMerchantAuth(request, options);
  const storeFilter =
    requestUrl.searchParams.get("store_id") ??
    requestUrl.searchParams.get("shop");
  const events = await store.list();
  const snapshots = [...buildOfferSnapshots(events).values()]
    .filter((record) => !storeFilter || record.store_id === storeFilter)
    .map(sanitizeOfferForInbox);
  jsonResponse(response, 200, { count: snapshots.length, offers: snapshots });
}

async function handleOfferDetailGet(
  transactionId,
  request,
  requestUrl,
  response,
  store,
  options,
) {
  verifyMerchantAuth(request, options);
  const storeId = normalizeString(
    requestUrl.searchParams.get("store_id") ??
      requestUrl.searchParams.get("shop"),
    "store_id",
  );
  const events = await store.list();
  const snapshot = getSnapshotOrThrow(events, transactionId, storeId);
  jsonResponse(response, 200, { offer: sanitizeOfferForInbox(snapshot) });
}

async function handleMerchantActionPost(
  transactionId,
  action,
  request,
  response,
  store,
  maxBodyBytes,
  options,
) {
  verifyMerchantAuth(request, options);
  const payload = normalizeMerchantPayload(
    await readJsonRequest(request, maxBodyBytes),
    action,
  );
  let buyerResponsePath = null;
  const { events } = await store.appendWithEvents((existingEvents) => {
    const snapshot = getSnapshotOrThrow(
      existingEvents,
      transactionId,
      payload.store_id,
    );
    const result = createMerchantActionEvent(
      snapshot,
      action,
      payload,
      options,
    );
    buyerResponsePath = result.buyerResponsePath;
    return result.event;
  });
  const snapshot = getSnapshotOrThrow(events, transactionId, payload.store_id);
  const body = { offer: sanitizeOfferForInbox(snapshot) };
  if (buyerResponsePath) {
    body.buyer_response_path = buyerResponsePath;
  }
  jsonResponse(response, 200, body);
}

function matchMerchantActionPath(pathname) {
  const match = pathname.match(
    /^\/counterpilot\/merchant\/offers\/([^/]+)\/(accept|counter|decline)$/,
  );
  if (!match) {
    return null;
  }
  return {
    transactionId: decodeURIComponent(match[1]),
    action: match[2],
  };
}

function matchMerchantDetailPath(pathname) {
  const match = pathname.match(/^\/counterpilot\/merchant\/offers\/([^/]+)$/);
  return match ? decodeURIComponent(match[1]) : null;
}

function matchBuyerRespondPath(pathname) {
  const match = pathname.match(
    /^\/apps\/counterpilot\/offers\/([^/]+)\/respond$/,
  );
  return match ? decodeURIComponent(match[1]) : null;
}

function matchBuyerAcceptPath(pathname) {
  const match = pathname.match(
    /^\/apps\/counterpilot\/offers\/([^/]+)\/accept$/,
  );
  return match ? decodeURIComponent(match[1]) : null;
}

async function handleBuyerResponseGet(
  transactionId,
  requestUrl,
  response,
  store,
  options,
) {
  const storeId = resolveAppProxyStoreId(requestUrl, {}, options);
  const token = requestUrl.searchParams.get("token");
  const events = await store.list();
  const snapshot = getSnapshotOrThrow(events, transactionId, storeId);
  validateBuyerResponseSnapshot(snapshot, token);
  jsonResponse(response, 200, sanitizeBuyerResponseView(snapshot));
}

async function handleBuyerAcceptPost(
  transactionId,
  requestUrl,
  response,
  store,
  options,
) {
  const storeId = resolveAppProxyStoreId(requestUrl, {}, options);
  const token = requestUrl.searchParams.get("token");
  const result = await store.transaction(
    async ({ events, checkoutRefs, appendEvent, appendCheckoutRef }) => {
      let snapshot = getSnapshotOrThrow(events, transactionId, storeId);
      validateBuyerResponseSnapshot(snapshot, token);

      if (snapshot.lifecycle_state === "checkout_created") {
        return {
          statusCode: 200,
          body: checkoutResponse(
            snapshot,
            latestCheckoutRefFor(checkoutRefs, transactionId),
          ),
        };
      }

      if (snapshot.lifecycle_state !== "buyer_accepted") {
        await appendEvent(createBuyerAcceptedEvent(snapshot));
        snapshot = getSnapshotOrThrow(events, transactionId, storeId);
      }

      if (!snapshot.operational_refs?.variant_ref) {
        const failure = {
          error_code: "missing_variant_ref",
          statusCode: 422,
          user_errors: [],
        };
        await appendEvent(createCheckoutCreationFailedEvent(snapshot, failure));
        snapshot = getSnapshotOrThrow(events, transactionId, storeId);
        return {
          statusCode: failure.statusCode,
          body: {
            error: "checkout_creation_failed",
            error_code: failure.error_code,
            offer: sanitizeOfferForInbox(snapshot),
          },
        };
      }

      if (snapshot.checkout_creation_status === "started") {
        return {
          statusCode: 202,
          body: {
            error: "checkout_creation_pending",
            offer: sanitizeOfferForInbox(snapshot),
          },
        };
      }

      try {
        await appendEvent(createCheckoutCreationStartedEvent(snapshot));
        snapshot = getSnapshotOrThrow(events, transactionId, storeId);
        const draftOrder = await checkoutAdapter(options)({
          shop: storeId,
          adminAccessToken: shopifyAdminAccessToken(options),
          apiVersion: shopifyApiVersion(options),
          transactionId,
          variantRef: snapshot.operational_refs.variant_ref,
          quantity: snapshot.quantity,
          acceptedUnitAmountMinor: snapshot.accepted_amount_minor,
          currency: snapshot.accepted_currency,
          productTitle: snapshot.product_title,
        });
        const checkoutEvent = await appendEvent(
          createCheckoutCreatedEvent(snapshot, draftOrder),
        );
        const checkoutRef = await appendCheckoutRef(
          createCheckoutRefRecord(snapshot, draftOrder),
        );
        snapshot = getSnapshotOrThrow(
          events,
          checkoutEvent.transaction_id,
          storeId,
        );
        return {
          statusCode: 200,
          body: checkoutResponse(snapshot, checkoutRef),
        };
      } catch (error) {
        const failure = checkoutFailureDetails(error);
        await appendEvent(createCheckoutCreationFailedEvent(snapshot, failure));
        snapshot = getSnapshotOrThrow(events, transactionId, storeId);
        return {
          statusCode: failure.statusCode,
          body: {
            error: "checkout_creation_failed",
            error_code: failure.error_code,
            user_errors: failure.user_errors,
            offer: sanitizeOfferForInbox(snapshot),
          },
        };
      }
    },
  );
  jsonResponse(response, result.statusCode, result.body);
}

async function handleShopifyOrderWebhookPost(
  request,
  response,
  store,
  maxBodyBytes,
  options,
) {
  const rawBody = await readRawRequest(request, maxBodyBytes);
  const webhook = normalizeShopifyOrderWebhook({
    rawBody,
    headers: request.headers,
    webhookSecret: shopifyWebhookSecret(options),
    productionEvidence: shopifyProductionEvidence(options),
  });

  const result = await store.transaction(
    async ({
      events,
      orderRefs,
      webhookDeliveries,
      appendEvent,
      appendOrderRef,
      appendWebhookDelivery,
    }) => {
      const existingDelivery = latestWebhookDeliveryFor(
        webhookDeliveries,
        webhook.deliveryId,
      );
      if (existingDelivery) {
        return {
          statusCode: 200,
          body: {
            received: true,
            duplicate: true,
            status: existingDelivery.status,
          },
        };
      }

      if (!webhook.transactionId) {
        await appendWebhookDelivery(
          createWebhookDeliveryRecord(
            webhook,
            "ignored_no_counterpilot_transaction",
          ),
        );
        return {
          statusCode: 200,
          body: { received: true, ignored: true },
        };
      }

      let snapshot = buildOfferSnapshots(events).get(webhook.transactionId);
      if (!snapshot) {
        await appendWebhookDelivery(
          createWebhookDeliveryRecord(webhook, "ignored_unknown_transaction"),
        );
        return {
          statusCode: 200,
          body: { received: true, ignored: true },
        };
      }
      if (snapshot.store_id !== webhook.shop) {
        throw validationError(
          "Shopify webhook shop does not match transaction store",
          403,
        );
      }
      if (!ORDER_WEBHOOK_READY_STATES.has(snapshot.lifecycle_state)) {
        return {
          statusCode: 409,
          body: {
            error: "checkout_not_created",
            transaction_id: webhook.transactionId,
          },
        };
      }

      const orderReferenceHash = checkoutRefHash(webhook.order.reference);
      if (!hasOrderRef(orderRefs, webhook.transactionId, orderReferenceHash)) {
        await appendOrderRef(createOrderRefRecord(snapshot, webhook));
      }

      const appended = {
        order_created: false,
        paid: false,
      };

      if (
        !hasTransactionEvent(events, webhook.transactionId, "order_created")
      ) {
        await appendEvent(createOrderCreatedEvent(snapshot, webhook));
        appended.order_created = true;
        snapshot = buildOfferSnapshots(events).get(webhook.transactionId);
      }

      if (
        webhook.paidObserved &&
        !hasTransactionEvent(events, webhook.transactionId, "paid")
      ) {
        await appendEvent(createPaidEvent(snapshot, webhook));
        appended.paid = true;
        snapshot = buildOfferSnapshots(events).get(webhook.transactionId);
      }

      await appendWebhookDelivery(
        createWebhookDeliveryRecord(webhook, "processed"),
      );

      return {
        statusCode: 200,
        body: {
          received: true,
          duplicate: false,
          transaction_id: webhook.transactionId,
          lifecycle_state: snapshot.lifecycle_state,
          appended,
        },
      };
    },
  );
  jsonResponse(response, result.statusCode, result.body);
}

async function handleShopifyRefundWebhookPost(
  request,
  response,
  store,
  maxBodyBytes,
  options,
) {
  const rawBody = await readRawRequest(request, maxBodyBytes);
  const refundWebhook = normalizeShopifyRefundWebhook({
    rawBody,
    headers: request.headers,
    webhookSecret: shopifyWebhookSecret(options),
    productionEvidence: shopifyProductionEvidence(options),
  });

  const result = await store.transaction(
    async ({
      events,
      orderRefs,
      refundRefs,
      webhookDeliveries,
      appendEvent,
      appendRefundRef,
      appendWebhookDelivery,
    }) => {
      const existingDelivery = latestWebhookDeliveryFor(
        webhookDeliveries,
        refundWebhook.deliveryId,
      );
      if (existingDelivery) {
        return {
          statusCode: 200,
          body: {
            received: true,
            duplicate: true,
            status: existingDelivery.status,
          },
        };
      }

      const orderReferenceHash = refundWebhook.order?.reference
        ? checkoutRefHash(refundWebhook.order.reference)
        : null;
      const refundReferenceHash = refundWebhook.refund?.reference
        ? checkoutRefHash(refundWebhook.refund.reference)
        : null;
      const orderRef = orderReferenceHash
        ? findOrderRefByHash(orderRefs, orderReferenceHash)
        : null;

      if (!orderRef) {
        await appendRefundRef(
          createRefundRefRecord({
            refundWebhook,
            orderReferenceHash,
            status: "ignored_unknown_order",
          }),
        );
        await appendWebhookDelivery(
          createRefundWebhookDeliveryRecord(
            refundWebhook,
            "ignored_unknown_order",
          ),
        );
        return {
          statusCode: 200,
          body: { received: true, ignored: true },
        };
      }

      const snapshot = buildOfferSnapshots(events).get(orderRef.transaction_id);
      if (!snapshot || snapshot.store_id !== refundWebhook.shop) {
        await appendRefundRef(
          createRefundRefRecord({
            refundWebhook,
            transactionId: orderRef.transaction_id,
            orderReferenceHash,
            status: "ignored_order_binding_mismatch",
          }),
        );
        await appendWebhookDelivery(
          createRefundWebhookDeliveryRecord(
            refundWebhook,
            "ignored_order_binding_mismatch",
            orderRef.transaction_id,
          ),
        );
        return {
          statusCode: 200,
          body: { received: true, ignored: true },
        };
      }

      if (!REFUND_WEBHOOK_READY_STATES.has(snapshot.lifecycle_state)) {
        await appendRefundRef(
          createRefundRefRecord({
            refundWebhook,
            transactionId: snapshot.transaction_id,
            orderReferenceHash,
            status: "held_before_paid",
          }),
        );
        await appendWebhookDelivery(
          createRefundWebhookDeliveryRecord(
            refundWebhook,
            "held_before_paid",
            snapshot.transaction_id,
          ),
        );
        return {
          statusCode: 202,
          body: {
            received: true,
            held: true,
            reason: "refund_before_paid",
          },
        };
      }

      if (
        refundWebhook.needsReconciliation ||
        !refundWebhook.refund.currency ||
        refundWebhook.refund.currency !== snapshot.paid_currency
      ) {
        await appendRefundRef(
          createRefundRefRecord({
            refundWebhook,
            transactionId: snapshot.transaction_id,
            orderReferenceHash,
            status: "needs_reconciliation",
          }),
        );
        await appendWebhookDelivery(
          createRefundWebhookDeliveryRecord(
            refundWebhook,
            "needs_reconciliation",
            snapshot.transaction_id,
          ),
        );
        return {
          statusCode: 202,
          body: {
            received: true,
            held: true,
            reason:
              refundWebhook.reconciliationReason ?? "refund_currency_mismatch",
          },
        };
      }

      if (
        hasProcessedRefundRef(
          refundRefs,
          snapshot.transaction_id,
          refundReferenceHash,
        )
      ) {
        await appendWebhookDelivery(
          createRefundWebhookDeliveryRecord(
            refundWebhook,
            "duplicate_refund",
            snapshot.transaction_id,
          ),
        );
        return {
          statusCode: 200,
          body: {
            received: true,
            duplicate_refund: true,
            transaction_id: snapshot.transaction_id,
          },
        };
      }

      await appendRefundRef(
        createRefundRefRecord({
          refundWebhook,
          transactionId: snapshot.transaction_id,
          orderReferenceHash,
          status: "processed",
        }),
      );
      const cumulativeRefundTotalMinor =
        priorRefundTotalMinor(events, snapshot.transaction_id) +
        refundWebhook.refund.refundTotalMinor;
      const refundEvent = await appendEvent(
        createRefundRecordedEvent({
          snapshot,
          refundWebhook,
          refundReferenceHash,
          orderReferenceHash,
          cumulativeRefundTotalMinor,
        }),
      );
      await appendWebhookDelivery(
        createRefundWebhookDeliveryRecord(
          refundWebhook,
          "processed",
          snapshot.transaction_id,
        ),
      );

      return {
        statusCode: 200,
        body: {
          received: true,
          duplicate: false,
          transaction_id: snapshot.transaction_id,
          lifecycle_state: refundEvent.lifecycle_state,
          refund_total_minor: refundEvent.refund_total_minor,
          cumulative_refund_total_minor:
            refundEvent.cumulative_refund_total_minor,
        },
      };
    },
  );
  jsonResponse(response, result.statusCode, result.body);
}

async function handleShopifyReturnWebhookPost(
  request,
  response,
  store,
  maxBodyBytes,
  options,
) {
  const rawBody = await readRawRequest(request, maxBodyBytes);
  const returnWebhook = normalizeShopifyReturnWebhook({
    rawBody,
    headers: request.headers,
    webhookSecret: shopifyWebhookSecret(options),
    productionEvidence: shopifyProductionEvidence(options),
  });

  const result = await store.transaction(
    async ({
      events,
      orderRefs,
      returnRefs,
      webhookDeliveries,
      appendEvent,
      appendReturnRef,
      appendWebhookDelivery,
    }) => {
      const existingDelivery = latestWebhookDeliveryFor(
        webhookDeliveries,
        returnWebhook.deliveryId,
      );
      if (existingDelivery) {
        return {
          statusCode: 200,
          body: {
            received: true,
            duplicate: true,
            status: existingDelivery.status,
          },
        };
      }

      const orderReferenceHash = returnWebhook.order?.reference
        ? checkoutRefHash(returnWebhook.order.reference)
        : null;
      const returnReferenceHash = returnWebhook.return?.reference
        ? checkoutRefHash(returnWebhook.return.reference)
        : null;
      const orderRef = orderReferenceHash
        ? findOrderRefByHash(orderRefs, orderReferenceHash)
        : null;

      if (!orderRef) {
        await appendReturnRef(
          createReturnRefRecord({
            returnWebhook,
            orderReferenceHash,
            status: "ignored_unknown_order",
          }),
        );
        await appendWebhookDelivery(
          createReturnWebhookDeliveryRecord(
            returnWebhook,
            "ignored_unknown_order",
          ),
        );
        return {
          statusCode: 200,
          body: { received: true, ignored: true },
        };
      }

      const snapshot = buildOfferSnapshots(events).get(orderRef.transaction_id);
      if (!snapshot || snapshot.store_id !== returnWebhook.shop) {
        await appendReturnRef(
          createReturnRefRecord({
            returnWebhook,
            transactionId: orderRef.transaction_id,
            orderReferenceHash,
            status: "ignored_order_binding_mismatch",
          }),
        );
        await appendWebhookDelivery(
          createReturnWebhookDeliveryRecord(
            returnWebhook,
            "ignored_order_binding_mismatch",
            orderRef.transaction_id,
          ),
        );
        return {
          statusCode: 200,
          body: { received: true, ignored: true },
        };
      }

      if (!RETURN_WEBHOOK_READY_STATES.has(snapshot.lifecycle_state)) {
        await appendReturnRef(
          createReturnRefRecord({
            returnWebhook,
            transactionId: snapshot.transaction_id,
            orderReferenceHash,
            status: "held_before_paid",
          }),
        );
        await appendWebhookDelivery(
          createReturnWebhookDeliveryRecord(
            returnWebhook,
            "held_before_paid",
            snapshot.transaction_id,
          ),
        );
        return {
          statusCode: 202,
          body: {
            received: true,
            held: true,
            reason: "return_before_paid",
          },
        };
      }

      const latestProcessedReturnRef = latestProcessedReturnStatusRef(
        returnRefs,
        snapshot.transaction_id,
        returnReferenceHash,
      );
      if (
        latestProcessedReturnRef?.return_status === returnWebhook.return.status
      ) {
        await appendWebhookDelivery(
          createReturnWebhookDeliveryRecord(
            returnWebhook,
            "duplicate_return_status",
            snapshot.transaction_id,
          ),
        );
        return {
          statusCode: 200,
          body: {
            received: true,
            duplicate_return_status: true,
            transaction_id: snapshot.transaction_id,
          },
        };
      }

      await appendReturnRef(
        createReturnRefRecord({
          returnWebhook,
          transactionId: snapshot.transaction_id,
          orderReferenceHash,
          status: "processed",
        }),
      );
      const returnEvent = await appendEvent(
        createReturnStatusRecordedEvent({
          snapshot,
          returnWebhook,
          returnReferenceHash,
          orderReferenceHash,
        }),
      );
      await appendWebhookDelivery(
        createReturnWebhookDeliveryRecord(
          returnWebhook,
          "processed",
          snapshot.transaction_id,
        ),
      );

      return {
        statusCode: 200,
        body: {
          received: true,
          duplicate: false,
          transaction_id: snapshot.transaction_id,
          lifecycle_state: snapshot.lifecycle_state,
          return_status: returnEvent.return_status,
          return_exposure_state: returnEvent.return_exposure_state,
        },
      };
    },
  );
  jsonResponse(response, result.statusCode, result.body);
}

export function createCounterpilotServer(options = {}) {
  const dataDir =
    options.dataDir ??
    process.env.COUNTERPILOT_SERVER_DATA_DIR ??
    process.env.COUNTERPILOT_DATA_DIR ??
    DEFAULT_DATA_DIR;
  const maxBodyBytes = options.maxBodyBytes ?? DEFAULT_MAX_BODY_BYTES;
  const webhookMaxBodyBytes =
    options.webhookMaxBodyBytes ?? DEFAULT_WEBHOOK_MAX_BODY_BYTES;
  const store = options.store ?? new OfferStore(dataDir);

  return http.createServer(async (request, response) => {
    const requestUrl = new URL(request.url, "http://127.0.0.1");
    try {
      if (request.method === "OPTIONS") {
        jsonResponse(response, 204, {});
        return;
      }
      if (request.method === "GET" && requestUrl.pathname === "/healthz") {
        jsonResponse(response, 200, { ok: true });
        return;
      }
      if (
        request.method === "POST" &&
        requestUrl.pathname === SHOPIFY_ORDER_WEBHOOK_PATH
      ) {
        await handleShopifyOrderWebhookPost(
          request,
          response,
          store,
          webhookMaxBodyBytes,
          options,
        );
        return;
      }
      if (
        request.method === "POST" &&
        requestUrl.pathname === SHOPIFY_REFUND_WEBHOOK_PATH
      ) {
        await handleShopifyRefundWebhookPost(
          request,
          response,
          store,
          webhookMaxBodyBytes,
          options,
        );
        return;
      }
      if (
        request.method === "POST" &&
        requestUrl.pathname === SHOPIFY_RETURN_WEBHOOK_PATH
      ) {
        await handleShopifyReturnWebhookPost(
          request,
          response,
          store,
          webhookMaxBodyBytes,
          options,
        );
        return;
      }
      if (
        request.method === "POST" &&
        OFFER_POST_PATHS.has(requestUrl.pathname)
      ) {
        await handleOfferPost(
          request,
          requestUrl,
          response,
          store,
          maxBodyBytes,
          options,
        );
        return;
      }
      const buyerResponseTransactionId = matchBuyerRespondPath(
        requestUrl.pathname,
      );
      if (request.method === "GET" && buyerResponseTransactionId) {
        await handleBuyerResponseGet(
          buyerResponseTransactionId,
          requestUrl,
          response,
          store,
          options,
        );
        return;
      }
      const buyerAcceptTransactionId = matchBuyerAcceptPath(
        requestUrl.pathname,
      );
      if (request.method === "POST" && buyerAcceptTransactionId) {
        await handleBuyerAcceptPost(
          buyerAcceptTransactionId,
          requestUrl,
          response,
          store,
          options,
        );
        return;
      }
      if (
        request.method === "GET" &&
        requestUrl.pathname === MERCHANT_INBOX_PATH
      ) {
        await handleInboxGet(request, requestUrl, response, store, options);
        return;
      }
      const actionPath = matchMerchantActionPath(requestUrl.pathname);
      if (request.method === "POST" && actionPath) {
        await handleMerchantActionPost(
          actionPath.transactionId,
          actionPath.action,
          request,
          response,
          store,
          maxBodyBytes,
          options,
        );
        return;
      }
      const detailTransactionId = matchMerchantDetailPath(requestUrl.pathname);
      if (request.method === "GET" && detailTransactionId) {
        await handleOfferDetailGet(
          detailTransactionId,
          request,
          requestUrl,
          response,
          store,
          options,
        );
        return;
      }
      jsonResponse(response, 404, { error: "not_found" });
    } catch (error) {
      const statusCode = error.statusCode ?? 500;
      jsonResponse(response, statusCode, {
        error: error.expose ? error.message : "internal_server_error",
      });
    }
  });
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const port = Number(process.env.PORT ?? 8787);
  const host = process.env.HOST ?? "127.0.0.1";
  const server = createCounterpilotServer();
  server.listen(port, host, () => {
    console.log(
      `Counterpilot offer server listening on http://${host}:${port}`,
    );
  });
}
