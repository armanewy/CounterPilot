import crypto from "node:crypto";

const SUPPORTED_TOPICS = new Set(["orders/create", "orders/paid"]);
const TRANSACTION_ATTRIBUTE = "counterpilot_transaction_id";

export class ShopifyWebhookError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = "ShopifyWebhookError";
    this.code = options.code ?? "shopify_webhook_error";
    this.statusCode = options.statusCode ?? 400;
    this.expose = true;
  }
}

export function getHeader(headers, name) {
  const wanted = name.toLowerCase();
  for (const [key, value] of Object.entries(headers ?? {})) {
    if (key.toLowerCase() === wanted) {
      return Array.isArray(value) ? value[0] : value;
    }
  }
  return undefined;
}

function requireHeader(headers, name) {
  const value = getHeader(headers, name);
  if (typeof value !== "string" || value.trim() === "") {
    throw new ShopifyWebhookError(`${name} header is required`, {
      code: `missing_${name.toLowerCase().replaceAll("-", "_")}`,
      statusCode: 401,
    });
  }
  return value.trim();
}

function requireSecret(secret) {
  if (typeof secret !== "string" || secret.trim() === "") {
    throw new ShopifyWebhookError("Shopify webhook secret is not configured", {
      code: "missing_webhook_secret",
      statusCode: 401,
    });
  }
  return secret;
}

function safeCompareBase64(actual, expected) {
  const actualBuffer = Buffer.from(String(actual), "base64");
  const expectedBuffer = Buffer.from(String(expected), "base64");
  if (
    actualBuffer.length === 0 ||
    actualBuffer.length !== expectedBuffer.length
  ) {
    return false;
  }
  return crypto.timingSafeEqual(actualBuffer, expectedBuffer);
}

export function verifyShopifyWebhookHmac(rawBody, headers, secret) {
  const normalizedSecret = requireSecret(secret);
  const receivedHmac = requireHeader(headers, "x-shopify-hmac-sha256");
  const calculatedHmac = crypto
    .createHmac("sha256", normalizedSecret)
    .update(rawBody)
    .digest("base64");
  if (!safeCompareBase64(receivedHmac, calculatedHmac)) {
    throw new ShopifyWebhookError("Shopify webhook HMAC is invalid", {
      code: "invalid_webhook_hmac",
      statusCode: 401,
    });
  }
  return receivedHmac;
}

function parsePayload(rawBody) {
  try {
    return JSON.parse(Buffer.from(rawBody).toString("utf8"));
  } catch {
    throw new ShopifyWebhookError("Shopify webhook body must be valid JSON", {
      code: "invalid_webhook_json",
      statusCode: 400,
    });
  }
}

function moneyMinor(value, fieldName, { required = false } = {}) {
  if (value === undefined || value === null || value === "") {
    if (required) {
      throw new ShopifyWebhookError(`${fieldName} is required`, {
        code: `missing_${fieldName}`,
        statusCode: 400,
      });
    }
    return 0;
  }
  const normalized = String(value).trim();
  if (!/^(0|[1-9]\d*)(\.\d{1,2})?$/.test(normalized)) {
    throw new ShopifyWebhookError(`${fieldName} must be a money amount`, {
      code: `invalid_${fieldName}`,
      statusCode: 400,
    });
  }
  const [units, cents = ""] = normalized.split(".");
  const amountMinor = Number(units) * 100 + Number(cents.padEnd(2, "0"));
  if (!Number.isSafeInteger(amountMinor)) {
    throw new ShopifyWebhookError(`${fieldName} is too large`, {
      code: `invalid_${fieldName}`,
      statusCode: 400,
    });
  }
  return amountMinor;
}

function normalizeCurrency(payload) {
  const currency = String(
    payload.presentment_currency ?? payload.currency ?? "USD",
  )
    .trim()
    .toUpperCase();
  if (!/^[A-Z]{3}$/.test(currency)) {
    throw new ShopifyWebhookError("Shopify order currency is invalid", {
      code: "invalid_currency",
      statusCode: 400,
    });
  }
  return currency;
}

function shippingMinor(payload) {
  return moneyMinor(
    payload.current_shipping_price_set?.shop_money?.amount ??
      payload.current_shipping_price_set?.presentment_money?.amount,
    "shipping_total",
  );
}

function transactionIdFromAttributes(attributes) {
  if (!Array.isArray(attributes)) {
    return null;
  }
  for (const attribute of attributes) {
    const key = attribute?.name ?? attribute?.key;
    if (key !== TRANSACTION_ATTRIBUTE) {
      continue;
    }
    const value = String(attribute?.value ?? "").trim();
    if (!/^cp_offer_[A-Za-z0-9_-]+$/.test(value)) {
      throw new ShopifyWebhookError(
        "counterpilot_transaction_id is malformed",
        {
          code: "invalid_counterpilot_transaction_id",
          statusCode: 400,
        },
      );
    }
    return value;
  }
  return null;
}

function transactionIdFromTags(tags) {
  const tagList = Array.isArray(tags)
    ? tags
    : String(tags ?? "")
        .split(",")
        .map((tag) => tag.trim());
  for (const tag of tagList) {
    const match = String(tag).match(
      /^counterpilot_transaction_id[:=](cp_offer_[A-Za-z0-9_-]+)$/,
    );
    if (match) {
      return match[1];
    }
  }
  return null;
}

function extractTransactionId(payload) {
  return (
    transactionIdFromAttributes(payload.note_attributes) ??
    transactionIdFromAttributes(payload.customAttributes) ??
    transactionIdFromTags(payload.tags)
  );
}

function orderReference(payload) {
  const orderGid =
    typeof payload.admin_graphql_api_id === "string"
      ? payload.admin_graphql_api_id.trim()
      : "";
  if (orderGid) {
    return orderGid;
  }
  if (
    payload.id !== undefined &&
    payload.id !== null &&
    String(payload.id).trim()
  ) {
    return `gid://shopify/Order/${String(payload.id).trim()}`;
  }
  throw new ShopifyWebhookError("Shopify order reference is required", {
    code: "missing_order_reference",
    statusCode: 400,
  });
}

function eventSource(topic) {
  return topic === "orders/paid"
    ? "shopify_orders_paid_webhook"
    : "shopify_orders_create_webhook";
}

function paidObserved(topic, payload) {
  return topic === "orders/paid" || payload.financial_status === "paid";
}

export function normalizeShopifyOrderWebhook({
  rawBody,
  headers,
  webhookSecret,
  productionEvidence = false,
}) {
  verifyShopifyWebhookHmac(rawBody, headers, webhookSecret);
  const topic = requireHeader(headers, "x-shopify-topic");
  if (!SUPPORTED_TOPICS.has(topic)) {
    throw new ShopifyWebhookError(
      `unsupported Shopify webhook topic: ${topic}`,
      {
        code: "unsupported_webhook_topic",
        statusCode: 400,
      },
    );
  }
  const deliveryId = requireHeader(headers, "x-shopify-webhook-id");
  const shop = requireHeader(headers, "x-shopify-shop-domain");
  const payload = parsePayload(rawBody);
  const transactionId = extractTransactionId(payload);
  const currency = normalizeCurrency(payload);
  const orderRef = orderReference(payload);
  const orderName = payload.name === undefined ? null : String(payload.name);

  return {
    deliveryId,
    topic,
    shop,
    transactionId,
    source: eventSource(topic),
    paidObserved: paidObserved(topic, payload),
    productionEvidence: Boolean(productionEvidence),
    occurredAt:
      payload.processed_at ??
      payload.created_at ??
      payload.updated_at ??
      new Date().toISOString(),
    paidAt:
      payload.processed_at ??
      payload.updated_at ??
      payload.created_at ??
      new Date().toISOString(),
    order: {
      rawOrderId: payload.id === undefined ? null : String(payload.id),
      adminGraphqlApiId:
        payload.admin_graphql_api_id === undefined
          ? null
          : String(payload.admin_graphql_api_id),
      name: orderName,
      reference: orderRef,
      nameReference: orderName,
      currency,
      orderTotalMinor: moneyMinor(payload.current_total_price, "order_total", {
        required: true,
      }),
      shippingTotalMinor: shippingMinor(payload),
      taxTotalMinor: moneyMinor(payload.current_total_tax, "tax_total"),
      discountTotalMinor: moneyMinor(
        payload.current_total_discounts,
        "discount_total",
      ),
      financialStatus:
        payload.financial_status === undefined
          ? null
          : String(payload.financial_status),
    },
  };
}
