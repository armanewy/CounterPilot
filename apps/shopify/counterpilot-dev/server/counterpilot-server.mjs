import crypto from "node:crypto";
import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_MAX_BODY_BYTES = 16 * 1024;
const DEFAULT_DATA_DIR = path.join(process.cwd(), ".counterpilot-data");
const DEFAULT_BUYER_RESPONSE_TTL_MS = 7 * 24 * 60 * 60 * 1000;

const OFFER_POST_PATHS = new Set([
  "/counterpilot/offers",
  "/apps/counterpilot/offers",
]);

const MERCHANT_INBOX_PATH = "/counterpilot/merchant/offers";

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

  async list() {
    await this.writeQueue;
    return this.#readDirect();
  }

  async #appendDirect(record) {
    await fs.mkdir(this.dataDir, { recursive: true });
    await fs.appendFile(this.filePath, `${JSON.stringify(record)}\n`, "utf8");
  }

  async #readDirect() {
    try {
      const text = await fs.readFile(this.filePath, "utf8");
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
  }
  return snapshots;
}

export function sanitizeOfferForInbox(record) {
  return {
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
    currency: record.currency,
    quantity: record.quantity,
    buyer_contact_reference: record.buyer_contact_reference,
  };
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
  if (!BUYER_RESPONSE_STATES.has(snapshot.lifecycle_state)) {
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
  const { record, events } = await store.appendWithEvents((existingEvents) => {
    const snapshot = getSnapshotOrThrow(existingEvents, transactionId, storeId);
    validateBuyerResponseSnapshot(snapshot, token);
    return createBuyerAcceptedEvent(snapshot);
  });
  const snapshot = getSnapshotOrThrow(events, record.transaction_id, storeId);
  jsonResponse(response, 200, { offer: sanitizeOfferForInbox(snapshot) });
}

export function createCounterpilotServer(options = {}) {
  const dataDir =
    options.dataDir ??
    process.env.COUNTERPILOT_SERVER_DATA_DIR ??
    DEFAULT_DATA_DIR;
  const maxBodyBytes = options.maxBodyBytes ?? DEFAULT_MAX_BODY_BYTES;
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
