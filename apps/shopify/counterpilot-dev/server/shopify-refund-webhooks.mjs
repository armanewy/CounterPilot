import {
  ShopifyWebhookError,
  getHeader,
  verifyShopifyWebhookHmac,
} from "./shopify-order-webhooks.mjs";

const REFUND_TOPIC = "refunds/create";

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
    throw new ShopifyWebhookError("Shopify refund webhook body must be JSON", {
      code: "invalid_refund_webhook_json",
      statusCode: 400,
    });
  }
}

function moneyFromAmount(value, fieldName) {
  if (value === undefined || value === null || value === "") {
    return null;
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

function moneyFromSet(value, fieldName) {
  const money = value?.shop_money ?? value?.presentment_money;
  if (!money) {
    return null;
  }
  const amountMinor = moneyFromAmount(money.amount, fieldName);
  const currency = normalizeCurrency(money.currency_code);
  return { amountMinor, currency };
}

function normalizeCurrency(value) {
  if (typeof value !== "string" || value.trim() === "") {
    return null;
  }
  const currency = value.trim().toUpperCase();
  return /^[A-Z]{3}$/.test(currency) ? currency : null;
}

function successfulRefundTransactions(transactions) {
  return (Array.isArray(transactions) ? transactions : []).filter(
    (transaction) =>
      String(transaction?.kind ?? "").toLowerCase() === "refund" &&
      String(transaction?.status ?? "").toLowerCase() === "success",
  );
}

function sumSuccessfulRefundTransactions(transactions) {
  const refundTransactions = successfulRefundTransactions(transactions);
  if (refundTransactions.length === 0) {
    return null;
  }
  const currencies = new Set();
  let totalMinor = 0;
  for (const transaction of refundTransactions) {
    const amountMinor = moneyFromAmount(
      transaction.amount,
      "refund_transaction_amount",
    );
    if (amountMinor === null) {
      return {
        needsReconciliation: true,
        reason: "missing_refund_transaction_amount",
      };
    }
    const currency = normalizeCurrency(transaction.currency);
    if (!currency) {
      return {
        needsReconciliation: true,
        reason: "missing_refund_transaction_currency",
      };
    }
    totalMinor += amountMinor;
    currencies.add(currency);
  }
  if (currencies.size !== 1) {
    return {
      needsReconciliation: true,
      reason:
        currencies.size === 0
          ? "missing_refund_transaction_currency"
          : "conflicting_refund_transaction_currency",
    };
  }
  return {
    amountMinor: totalMinor,
    currency: [...currencies][0],
    source: "successful_refund_transactions",
  };
}

function sumFallbackRefundAmount(payload) {
  const currencies = new Set();
  let totalMinor = 0;
  let observedMoney = false;

  for (const item of Array.isArray(payload.refund_line_items)
    ? payload.refund_line_items
    : []) {
    for (const [field, label] of [
      ["subtotal_set", "refund_line_item_subtotal"],
      ["total_tax_set", "refund_line_item_tax"],
    ]) {
      const money = moneyFromSet(item?.[field], label);
      if (!money) {
        continue;
      }
      observedMoney = true;
      if (!money.currency) {
        return {
          needsReconciliation: true,
          reason: "missing_refund_currency",
        };
      }
      totalMinor += money.amountMinor ?? 0;
      currencies.add(money.currency);
    }
  }

  for (const line of Array.isArray(payload.refund_shipping_lines)
    ? payload.refund_shipping_lines
    : []) {
    const money =
      moneyFromSet(line?.subtotal_set, "refund_shipping_subtotal") ??
      moneyFromSet(line?.amount_set, "refund_shipping_amount");
    if (!money) {
      continue;
    }
    observedMoney = true;
    if (!money.currency) {
      return {
        needsReconciliation: true,
        reason: "missing_refund_currency",
      };
    }
    totalMinor += money.amountMinor ?? 0;
    currencies.add(money.currency);
  }

  for (const adjustment of Array.isArray(payload.order_adjustments)
    ? payload.order_adjustments
    : []) {
    const money =
      moneyFromSet(adjustment?.amount_set, "refund_adjustment_amount") ??
      moneyFromSet(adjustment?.tax_amount_set, "refund_adjustment_tax_amount");
    if (!money) {
      continue;
    }
    observedMoney = true;
    if (!money.currency) {
      return {
        needsReconciliation: true,
        reason: "missing_refund_currency",
      };
    }
    totalMinor += money.amountMinor ?? 0;
    currencies.add(money.currency);
  }

  if (!observedMoney) {
    return {
      needsReconciliation: true,
      reason: "missing_refund_amount",
    };
  }
  if (currencies.size !== 1) {
    return {
      needsReconciliation: true,
      reason:
        currencies.size === 0
          ? "missing_refund_currency"
          : "conflicting_refund_currency",
    };
  }
  return {
    amountMinor: totalMinor,
    currency: [...currencies][0],
    source: "line_item_fallback",
  };
}

function refundAmount(payload) {
  return (
    sumSuccessfulRefundTransactions(payload.transactions) ??
    sumFallbackRefundAmount(payload)
  );
}

function orderReference(payload) {
  const orderId = payload.order_id;
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

function refundReference(payload) {
  const refundGid =
    typeof payload.admin_graphql_api_id === "string"
      ? payload.admin_graphql_api_id.trim()
      : "";
  if (refundGid) {
    return refundGid;
  }
  if (
    payload.id !== undefined &&
    payload.id !== null &&
    String(payload.id).trim()
  ) {
    return `gid://shopify/Refund/${String(payload.id).trim()}`;
  }
  throw new ShopifyWebhookError("Shopify refund reference is required", {
    code: "missing_refund_reference",
    statusCode: 400,
  });
}

function rawRefundTransactionRefs(payload) {
  return (Array.isArray(payload.transactions) ? payload.transactions : []).map(
    (transaction) => ({
      id: transaction?.id === undefined ? null : String(transaction.id),
      admin_graphql_api_id:
        transaction?.admin_graphql_api_id === undefined
          ? null
          : String(transaction.admin_graphql_api_id),
      kind: transaction?.kind === undefined ? null : String(transaction.kind),
      status:
        transaction?.status === undefined ? null : String(transaction.status),
    }),
  );
}

export function normalizeShopifyRefundWebhook({
  rawBody,
  headers,
  webhookSecret,
  productionEvidence = false,
}) {
  verifyShopifyWebhookHmac(rawBody, headers, webhookSecret);
  const topic = requireHeader(headers, "x-shopify-topic");
  if (topic !== REFUND_TOPIC) {
    throw new ShopifyWebhookError(
      `unsupported Shopify webhook topic: ${topic}`,
      {
        code: "unsupported_refund_webhook_topic",
        statusCode: 400,
      },
    );
  }
  const deliveryId = requireHeader(headers, "x-shopify-webhook-id");
  const shop = requireHeader(headers, "x-shopify-shop-domain");
  const payload = parsePayload(rawBody);
  const normalizedOrderReference = orderReference(payload);
  if (!normalizedOrderReference) {
    return {
      deliveryId,
      topic,
      shop,
      needsReconciliation: true,
      reconciliationReason: "missing_order_reference",
      productionEvidence: Boolean(productionEvidence),
      refund: {
        rawRefundId: payload.id === undefined ? null : String(payload.id),
        adminGraphqlApiId:
          payload.admin_graphql_api_id === undefined
            ? null
            : String(payload.admin_graphql_api_id),
        reference: refundReference(payload),
        transactionRefs: rawRefundTransactionRefs(payload),
      },
    };
  }

  const amount = refundAmount(payload);
  return {
    deliveryId,
    topic,
    shop,
    source: "shopify_refunds_create_webhook",
    needsReconciliation: Boolean(amount.needsReconciliation),
    reconciliationReason: amount.reason ?? null,
    productionEvidence: Boolean(productionEvidence),
    processedAt:
      payload.processed_at ??
      payload.created_at ??
      payload.updated_at ??
      new Date().toISOString(),
    order: {
      rawOrderId:
        payload.order_id === undefined || payload.order_id === null
          ? null
          : String(payload.order_id),
      reference: normalizedOrderReference,
    },
    refund: {
      rawRefundId: payload.id === undefined ? null : String(payload.id),
      adminGraphqlApiId:
        payload.admin_graphql_api_id === undefined
          ? null
          : String(payload.admin_graphql_api_id),
      reference: refundReference(payload),
      refundTotalMinor: amount.amountMinor ?? null,
      currency: amount.currency ?? null,
      amountSource: amount.source ?? null,
      transactionRefs: rawRefundTransactionRefs(payload),
    },
  };
}
