# Shopify Development Store Runbook

This is the Wave 2 MarginPilot adapter path. It is a development-store
integration, not a public Shopify app, billing flow, recommendation engine, or
automated pricing system.

Docs checked on 2026-06-22:

- Theme app extensions are Shopify's supported mechanism for app blocks in
  Online Store themes: https://shopify.dev/docs/apps/build/online-store/theme-app-extensions/configuration
- `draftOrderCreate` is the GraphQL Admin mutation used to create draft
  orders: https://shopify.dev/docs/api/admin-graphql/latest/mutations/draftOrderCreate
- Draft orders expose an `invoiceUrl` secure checkout link:
  https://shopify.dev/docs/api/admin-graphql/latest/objects/DraftOrder
- Shopify webhook deliveries should be verified with
  `X-Shopify-Hmac-SHA256` and deduplicated using delivery IDs:
  https://shopify.dev/docs/apps/build/webhooks/verify-deliveries

## Local Files

```text
integrations/shopify/
  adapter.py
  fake_provider.py
  provider.py
  token_store.py
  webhooks.py
  theme_extension/
    blocks/make_offer.liquid
    blocks/cart_offer.liquid
    assets/marginpilot-offer.js
    assets/marginpilot-offer.css
```

## Storefront Surface

The theme extension exposes a product-page `Make an Offer` block and a disabled
by default cart-level offer block. The blocks collect only offer amount and
product/cart context. They do not collect names, emails, addresses, phone
numbers, buyer messages, or inferred identity.

The JavaScript dispatches a local `marginpilot:offer-submitted` event. A real
app server can translate that event into `ShopifyDevelopmentAdapter.submit_offer`
after validating merchant configuration and rate limits.

## Merchant Admin Surface

The adapter provides the development-mode admin actions:

- `offer_inbox`
- `merchant_accept`
- `merchant_decline`
- `merchant_counter`
- `expire_offer`

Each action writes through the existing MarginPilot state machine. Merchant
floor, cost basis, shipping discount, and fulfillment assumptions are explicit
economic fields. Recommendations remain `manual_only`.

## Checkout

`GraphQLShopifyProvider.create_discounted_draft_order` calls Shopify's Admin
GraphQL API with `draftOrderCreate`. Tests use
`DeterministicFakeShopifyProvider`; no live credentials are needed or allowed in
tests.

Raw Shopify resource IDs, access tokens, contact email, and invoice URLs belong
in encrypted operational storage. Adapter responses and research exports use
references such as `operational_store`, not raw Shopify IDs.

Checkout creation is gated by the MarginPilot state machine. The adapter
requires merchant or buyer acceptance before it calls the Shopify provider. A
duplicate checkout request returns the existing operational checkout reference
and does not create a second draft order.

## Webhooks

Transaction webhooks supported by the adapter:

- `orders/create`
- `orders/created`
- `orders/paid`
- `orders/updated`
- `orders/cancelled`
- `orders/cancel`
- `refunds/create`
- `refunds/created`
- `returns/open`
- `returns/opened`
- `returns/close`
- `returns/closed`

App-level webhooks supported separately:

- `app/uninstalled`
- `customers/data_request`
- `customers/redact`
- `shop/redact`

Every webhook is verified against the raw body using HMAC-SHA256 before the
payload is trusted. Delivery IDs become idempotency keys in the state machine.
Out-of-order transaction webhooks may be stored as pending and reconciled when
the predecessor event arrives.

Transaction webhooks must also include the Shopify shop-domain header and
resource IDs that bind the delivery to the encrypted operational record. Order
creation must match the draft order or checkout GID already stored
operationally; later order, refund, return, and cancellation events must match
the operational order GID. The raw Shopify resource IDs are used only for this
operational binding and are not copied into transaction events, reports, model
features, or research exports.

App-level webhooks are shop-bound too. The adapter verifies the same shop-domain
header against the encrypted installed-token record before token revocation or
compliance acknowledgement.

Mature contribution margin must reconcile to observed facts: the final sale
price must match the accepted checkout amount and the order/payment economics
recorded by Shopify webhooks before the return/refund maturity event is allowed
to enter research export.

## Security Boundary

- Access tokens are stored only via `ShopifyTokenStore` behind the encrypted
  adapter.
- Token install responses do not include the token.
- The required development scopes are intentionally narrow:
  `read_orders`, `read_products`, and `write_draft_orders`.
- No test uses live credentials.
- Merchant/store namespace is part of every transaction and token record.
- Operational data and research data remain separate.

## Development E2E

Run the deterministic no-credential proof:

```powershell
python -m pytest tests\shopify\test_shopify_adapter.py tests\test_marginpilot_e2e.py -q
```

The E2E writes a redacted report to
`docs/runs/MARGINPILOT_E2E_REPORT.md`. It must show no model
recommendations and no operational PII in the research projection.
