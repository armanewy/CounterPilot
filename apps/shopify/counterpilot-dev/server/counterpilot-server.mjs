import crypto from "node:crypto";
import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_MAX_BODY_BYTES = 16 * 1024;
const DEFAULT_DATA_DIR = path.join(process.cwd(), ".counterpilot-data");

const OFFER_POST_PATHS = new Set([
  "/counterpilot/offers",
  "/apps/counterpilot/offers"
]);

const INBOX_PATHS = new Set([
  "/counterpilot/merchant/offers",
  "/apps/counterpilot/merchant/offers"
]);

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
  "buyer_contact_token"
]);

const FORBIDDEN_OFFER_FIELDS = new Set([
  "address",
  "buyer_message",
  "checkout_url",
  "customer_email",
  "customer_name",
  "message",
  "note",
  "notes",
  "phone",
  "raw_buyer_email",
  "refresh_token",
  "shipping_address",
  "token",
  "access_token"
]);

class OfferStore {
  constructor(dataDir) {
    this.dataDir = dataDir;
    this.filePath = path.join(dataDir, "offers.jsonl");
    this.writeQueue = Promise.resolve();
  }

  async append(record) {
    this.writeQueue = this.writeQueue.then(async () => {
      await fs.mkdir(this.dataDir, { recursive: true });
      await fs.appendFile(this.filePath, `${JSON.stringify(record)}\n`, "utf8");
    });
    return this.writeQueue;
  }

  async list() {
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
  return crypto.createHash("sha256").update(String(value), "utf8").digest("hex");
}

function jsonResponse(response, statusCode, body) {
  const text = JSON.stringify(body);
  response.writeHead(statusCode, {
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Origin": "*",
    "Content-Length": Buffer.byteLength(text),
    "Content-Type": "application/json; charset=utf-8"
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
  const normalized = typeof value === "number" ? String(value) : normalizeString(value, fieldName, 32);
  if (!/^[1-9]\d*$/.test(normalized)) {
    throw validationError(`${fieldName} must be a positive integer`);
  }
  const parsed = Number(normalized);
  if (!Number.isSafeInteger(parsed)) {
    throw validationError(`${fieldName} is too large`);
  }
  return parsed;
}

function parseOfferAmountMinor(value) {
  const normalized = typeof value === "number" ? String(value) : normalizeString(value, "offer_amount", 32);
  if (!/^(0|[1-9]\d*)(\.\d{1,2})?$/.test(normalized)) {
    throw validationError("offer_amount must be a positive decimal with at most two cents digits");
  }
  const [units, cents = ""] = normalized.split(".");
  const amountMinor = Number(units) * 100 + Number(cents.padEnd(2, "0"));
  if (!Number.isSafeInteger(amountMinor) || amountMinor <= 0) {
    throw validationError("offer_amount must be greater than zero");
  }
  return amountMinor;
}

function validationError(message, statusCode = 400) {
  const error = new Error(message);
  error.statusCode = statusCode;
  error.expose = true;
  return error;
}

function validateAllowedFields(payload) {
  for (const key of Object.keys(payload)) {
    const lowerKey = key.toLowerCase();
    if (FORBIDDEN_OFFER_FIELDS.has(lowerKey)) {
      throw validationError(`${key} is not accepted by the offer intake route`);
    }
    if (!ALLOWED_OFFER_FIELDS.has(key)) {
      throw validationError(`${key} is not a supported offer field`);
    }
  }
}

function normalizeBuyerContact(payload) {
  const hasEmail = typeof payload.buyer_email === "string" && payload.buyer_email.trim() !== "";
  const hasToken = typeof payload.buyer_contact_token === "string" && payload.buyer_contact_token.trim() !== "";
  if (hasEmail && hasToken) {
    throw validationError("provide buyer_email or buyer_contact_token, not both");
  }
  if (!hasEmail && !hasToken) {
    throw validationError("buyer_email or buyer_contact_token is required");
  }
  if (hasEmail) {
    const email = normalizeString(payload.buyer_email, "buyer_email", 320).toLowerCase();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      throw validationError("buyer_email must be a valid email address");
    }
    return {
      buyer_contact_hash: `sha256:${hashValue(email)}`,
      buyer_contact_reference: `email_hash:${hashValue(email).slice(0, 16)}`,
      buyer_contact_type: "email"
    };
  }
  const token = normalizeString(payload.buyer_contact_token, "buyer_contact_token", 512);
  return {
    buyer_contact_hash: `sha256:${hashValue(token)}`,
    buyer_contact_reference: `token_hash:${hashValue(token).slice(0, 16)}`,
    buyer_contact_type: "token"
  };
}

export function normalizeOfferPayload(payload, now = new Date()) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw validationError("request body must be a JSON object");
  }
  validateAllowedFields(payload);

  const storeId = normalizeString(payload.store_id ?? payload.shop, "store_id");
  const productRef = normalizeString(payload.product_ref ?? payload.product_gid, "product_ref", 512);
  const variantRef = payload.variant_ref ?? payload.variant_gid;
  const productTitle = payload.product_title === undefined
    ? null
    : normalizeString(payload.product_title, "product_title", 255);
  const currency = payload.currency === undefined
    ? "USD"
    : normalizeString(payload.currency, "currency", 3).toUpperCase();
  if (!/^[A-Z]{3}$/.test(currency)) {
    throw validationError("currency must be a three-letter ISO currency code");
  }

  const buyerContact = normalizeBuyerContact(payload);
  const occurredAt = now.toISOString();
  return {
    schema_version: "counterpilot.server_offer.v1",
    transaction_id: `cp_offer_${crypto.randomUUID()}`,
    lifecycle_state: "offer_submitted",
    event_type: "offer_submitted",
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
    offer_amount_minor: parseOfferAmountMinor(payload.offer_amount),
    currency,
    quantity: parsePositiveInteger(payload.quantity ?? 1, "quantity"),
    buyer_contact_hash: buyerContact.buyer_contact_hash,
    buyer_contact_reference: buyerContact.buyer_contact_reference,
    buyer_contact_type: buyerContact.buyer_contact_type,
    operational_refs: {
      product_ref: productRef,
      variant_ref: variantRef ? normalizeString(variantRef, "variant_ref", 512) : null
    }
  };
}

export function sanitizeOfferForInbox(record) {
  return {
    transaction_id: record.transaction_id,
    lifecycle_state: record.lifecycle_state,
    event_type: record.event_type,
    submitted_at: record.occurred_at,
    store_id: record.store_id,
    product_title: record.product_title,
    product_reference_hash: record.product_reference_hash,
    variant_reference_hash: record.variant_reference_hash,
    offer_amount_minor: record.offer_amount_minor,
    currency: record.currency,
    quantity: record.quantity,
    buyer_contact_reference: record.buyer_contact_reference
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

async function handleOfferPost(request, response, store, maxBodyBytes) {
  const payload = await readJsonRequest(request, maxBodyBytes);
  const record = normalizeOfferPayload(payload);
  await store.append(record);
  jsonResponse(response, 201, {
    received: true,
    transaction_id: record.transaction_id,
    lifecycle_state: record.lifecycle_state,
    offer_amount_minor: record.offer_amount_minor,
    currency: record.currency,
    quantity: record.quantity,
    product_title: record.product_title
  });
}

async function handleInboxGet(requestUrl, response, store) {
  const storeFilter = requestUrl.searchParams.get("store_id") ?? requestUrl.searchParams.get("shop");
  const records = await store.list();
  const offers = records
    .filter((record) => record.lifecycle_state === "offer_submitted")
    .filter((record) => !storeFilter || record.store_id === storeFilter)
    .map(sanitizeOfferForInbox);
  jsonResponse(response, 200, { count: offers.length, offers });
}

export function createCounterpilotServer(options = {}) {
  const dataDir = options.dataDir ?? process.env.COUNTERPILOT_SERVER_DATA_DIR ?? DEFAULT_DATA_DIR;
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
      if (request.method === "POST" && OFFER_POST_PATHS.has(requestUrl.pathname)) {
        await handleOfferPost(request, response, store, maxBodyBytes);
        return;
      }
      if (request.method === "GET" && INBOX_PATHS.has(requestUrl.pathname)) {
        await handleInboxGet(requestUrl, response, store);
        return;
      }
      jsonResponse(response, 404, { error: "not_found" });
    } catch (error) {
      const statusCode = error.statusCode ?? 500;
      jsonResponse(response, statusCode, {
        error: error.expose ? error.message : "internal_server_error"
      });
    }
  });
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const port = Number(process.env.PORT ?? 8787);
  const host = process.env.HOST ?? "127.0.0.1";
  const server = createCounterpilotServer();
  server.listen(port, host, () => {
    console.log(`Counterpilot offer server listening on http://${host}:${port}`);
  });
}
