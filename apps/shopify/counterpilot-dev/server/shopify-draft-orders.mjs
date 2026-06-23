const DEFAULT_API_VERSION = "2026-04";

const DRAFT_ORDER_CREATE_MUTATION = `#graphql
mutation CounterpilotDraftOrderCreate($input: DraftOrderInput!) {
  draftOrderCreate(input: $input) {
    draftOrder {
      id
      invoiceUrl
    }
    userErrors {
      field
      message
    }
  }
}`;

export class ShopifyDraftOrderError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = "ShopifyDraftOrderError";
    this.code = options.code ?? "shopify_draft_order_error";
    this.statusCode = options.statusCode ?? 502;
    this.safeUserErrors = options.safeUserErrors ?? [];
    this.expose = true;
  }
}

export function formatMinorUnits(amountMinor) {
  if (!Number.isSafeInteger(amountMinor) || amountMinor <= 0) {
    throw new ShopifyDraftOrderError("accepted amount must be positive", {
      code: "invalid_accepted_amount",
      statusCode: 400,
    });
  }
  const units = Math.floor(amountMinor / 100);
  const cents = String(amountMinor % 100).padStart(2, "0");
  return `${units}.${cents}`;
}

function requireString(value, fieldName) {
  if (typeof value !== "string" || value.trim() === "") {
    throw new ShopifyDraftOrderError(`${fieldName} is required`, {
      code: `missing_${fieldName}`,
      statusCode: 400,
    });
  }
  return value.trim();
}

export function buildDraftOrderInput({
  transactionId,
  variantRef,
  quantity,
  acceptedUnitAmountMinor,
  currency,
}) {
  const normalizedTransactionId = requireString(
    transactionId,
    "transaction_id",
  );
  const normalizedVariantRef = requireString(variantRef, "variant_ref");
  const normalizedCurrency = requireString(currency, "currency").toUpperCase();
  if (!Number.isSafeInteger(quantity) || quantity <= 0) {
    throw new ShopifyDraftOrderError("quantity must be a positive integer", {
      code: "invalid_quantity",
      statusCode: 400,
    });
  }
  if (!/^[A-Z]{3}$/.test(normalizedCurrency)) {
    throw new ShopifyDraftOrderError("currency must be a three-letter code", {
      code: "invalid_currency",
      statusCode: 400,
    });
  }

  return {
    presentmentCurrencyCode: normalizedCurrency,
    acceptAutomaticDiscounts: false,
    allowDiscountCodesInCheckout: false,
    lineItems: [
      {
        variantId: normalizedVariantRef,
        quantity,
        priceOverride: {
          amount: formatMinorUnits(acceptedUnitAmountMinor),
          currencyCode: normalizedCurrency,
        },
      },
    ],
    customAttributes: [
      {
        key: "counterpilot_transaction_id",
        value: normalizedTransactionId,
      },
    ],
    tags: ["counterpilot", "counterpilot-negotiated"],
    visibleToCustomer: true,
  };
}

function safeUserErrors(userErrors) {
  return (Array.isArray(userErrors) ? userErrors : []).map((error) => ({
    field: Array.isArray(error.field)
      ? error.field.map((part) => String(part)).join(".")
      : null,
    message:
      typeof error.message === "string" ? error.message : "Shopify user error",
  }));
}

export async function createDraftOrderForAcceptedOffer({
  shop,
  adminAccessToken,
  apiVersion = DEFAULT_API_VERSION,
  transactionId,
  variantRef,
  quantity,
  acceptedUnitAmountMinor,
  currency,
  fetchImpl = fetch,
}) {
  const normalizedShop = requireString(shop, "shop");
  const normalizedToken = requireString(adminAccessToken, "admin_access_token");
  const normalizedApiVersion = requireString(apiVersion, "api_version");
  const input = buildDraftOrderInput({
    transactionId,
    variantRef,
    quantity,
    acceptedUnitAmountMinor,
    currency,
  });

  const response = await fetchImpl(
    `https://${normalizedShop}/admin/api/${normalizedApiVersion}/graphql.json`,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-shopify-access-token": normalizedToken,
      },
      body: JSON.stringify({
        query: DRAFT_ORDER_CREATE_MUTATION,
        variables: { input },
      }),
    },
  );

  let body;
  try {
    body = await response.json();
  } catch {
    throw new ShopifyDraftOrderError("Shopify returned invalid JSON", {
      code: "shopify_invalid_json",
      statusCode: 502,
    });
  }

  if (!response.ok) {
    throw new ShopifyDraftOrderError("Shopify draft order request failed", {
      code: "shopify_http_error",
      statusCode: 502,
    });
  }
  if (Array.isArray(body.errors) && body.errors.length > 0) {
    throw new ShopifyDraftOrderError("Shopify GraphQL request failed", {
      code: "shopify_graphql_error",
      statusCode: 502,
    });
  }

  const payload = body.data?.draftOrderCreate;
  const userErrors = safeUserErrors(payload?.userErrors);
  if (userErrors.length > 0) {
    throw new ShopifyDraftOrderError("Shopify rejected the draft order", {
      code: "shopify_user_error",
      statusCode: 422,
      safeUserErrors: userErrors,
    });
  }

  const draftOrderId = payload?.draftOrder?.id;
  const checkoutUrl = payload?.draftOrder?.invoiceUrl;
  if (!draftOrderId || !checkoutUrl) {
    throw new ShopifyDraftOrderError(
      "Shopify draft order response was incomplete",
      {
        code: "shopify_incomplete_response",
        statusCode: 502,
      },
    );
  }

  return {
    draftOrderId,
    checkoutUrl,
    input,
  };
}
