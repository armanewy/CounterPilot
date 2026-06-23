import {
  ShopifyWebhookError,
  getHeader,
  verifyShopifyWebhookHmac,
} from "./shopify-order-webhooks.mjs";

const TOPIC_EXPOSURE_STATES = new Map([
  ["returns/request", "open"],
  ["returns/approve", "open"],
  ["returns/reopen", "open"],
  ["returns/close", "closed"],
  ["returns/decline", "closed"],
  ["returns/cancel", "closed"],
]);

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

function parsePayload(rawBody) {
  try {
    return JSON.parse(Buffer.from(rawBody).toString("utf8"));
  } catch {
    throw new ShopifyWebhookError("Shopify return webhook body must be JSON", {
      code: "invalid_return_webhook_json",
      statusCode: 400,
    });
  }
}

function eventSource(topic) {
  return `shopify_${topic.replaceAll("/", "_")}_webhook`;
}

function orderReference(payload) {
  const orderGid =
    typeof payload.order?.admin_graphql_api_id === "string"
      ? payload.order.admin_graphql_api_id.trim()
      : "";
  if (orderGid) {
    return orderGid;
  }
  const orderId = payload.order_id ?? payload.order?.id;
  if (
    orderId === undefined ||
    orderId === null ||
    String(orderId).trim() === ""
  ) {
    return null;
  }
  const normalized = String(orderId).trim();
  return normalized.startsWith("gid://shopify/Order/")
    ? normalized
    : `gid://shopify/Order/${normalized}`;
}

function returnReference(payload) {
  const returnGid =
    typeof payload.admin_graphql_api_id === "string"
      ? payload.admin_graphql_api_id.trim()
      : "";
  if (returnGid) {
    return returnGid;
  }
  if (
    payload.id !== undefined &&
    payload.id !== null &&
    String(payload.id).trim()
  ) {
    return `gid://shopify/Return/${String(payload.id).trim()}`;
  }
  throw new ShopifyWebhookError("Shopify return reference is required", {
    code: "missing_return_reference",
    statusCode: 400,
  });
}

function rawOrderId(payload) {
  const value = payload.order_id ?? payload.order?.id;
  return value === undefined || value === null ? null : String(value);
}

function rawOrderAdminGraphqlApiId(payload) {
  return payload.order?.admin_graphql_api_id === undefined
    ? null
    : String(payload.order.admin_graphql_api_id);
}

function rawReturnStatus(payload) {
  return payload.status === undefined || payload.status === null
    ? null
    : String(payload.status).trim().toLowerCase();
}

function totalReturnLineItems(payload) {
  if (
    payload.total_return_line_items === undefined ||
    payload.total_return_line_items === null ||
    payload.total_return_line_items === ""
  ) {
    return null;
  }
  const value = Number(payload.total_return_line_items);
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new ShopifyWebhookError(
      "Shopify return total_return_line_items is invalid",
      {
        code: "invalid_total_return_line_items",
        statusCode: 400,
      },
    );
  }
  return value;
}

export function normalizeShopifyReturnWebhook({
  rawBody,
  headers,
  webhookSecret,
  productionEvidence = false,
}) {
  verifyShopifyWebhookHmac(rawBody, headers, webhookSecret);
  const topic = requireHeader(headers, "x-shopify-topic");
  if (!TOPIC_EXPOSURE_STATES.has(topic)) {
    throw new ShopifyWebhookError(
      `unsupported Shopify return webhook topic: ${topic}`,
      {
        code: "unsupported_return_webhook_topic",
        statusCode: 400,
      },
    );
  }
  const deliveryId = requireHeader(headers, "x-shopify-webhook-id");
  const shop = requireHeader(headers, "x-shopify-shop-domain");
  const payload = parsePayload(rawBody);
  const normalizedOrderReference = orderReference(payload);

  return {
    deliveryId,
    topic,
    shop,
    source: eventSource(topic),
    productionEvidence: Boolean(productionEvidence),
    occurredAt:
      payload.updated_at ??
      payload.created_at ??
      payload.processed_at ??
      new Date().toISOString(),
    order: normalizedOrderReference
      ? {
          rawOrderId: rawOrderId(payload),
          adminGraphqlApiId: rawOrderAdminGraphqlApiId(payload),
          reference: normalizedOrderReference,
        }
      : null,
    return: {
      rawReturnId: payload.id === undefined ? null : String(payload.id),
      adminGraphqlApiId:
        payload.admin_graphql_api_id === undefined
          ? null
          : String(payload.admin_graphql_api_id),
      name: payload.name === undefined ? null : String(payload.name),
      reference: returnReference(payload),
      status: rawReturnStatus(payload),
      exposureState: TOPIC_EXPOSURE_STATES.get(topic),
      totalReturnLineItems: totalReturnLineItems(payload),
    },
  };
}
