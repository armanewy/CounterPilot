# Counterpilot Shopify App Shell

This directory contains the Shopify CLI app shell for Counterpilot.

Counterpilot is a Shopify make-an-offer app with one golden path:

```text
shopper submits product-page offer
-> merchant reviews in Counterpilot inbox
-> merchant accepts, counters, or declines
-> buyer accepts counter
-> Shopify draft order / checkout is created
-> paid/refund/return webhooks are ingested
-> maturity window closes
-> merchant sees true mature margin
```

This app shell currently contains the product-page theme app extension and a
minimal local offer intake server used in the development-store proof. It is
not the complete production app server yet. The next implementation step is
maturity jobs and report generation.

## Current Extension

```text
extensions/counterpilot-offer-surface/
  blocks/make_offer.liquid
  blocks/cart_offer.liquid
  assets/counterpilot-offer.js
  assets/counterpilot-offer.css
```

Only product-page offers are in scope for the first beta. The cart block exists
from earlier scaffolding but should remain disabled unless explicitly tested.

## Setup

Install dependencies:

```shell
npm install
```

Build the Shopify app:

```shell
npm run build
```

Run the local Counterpilot offer intake server:

```shell
npm run counterpilot:server
```

The local server exposes:

```text
POST /counterpilot/offers
POST /apps/counterpilot/offers
GET /counterpilot/merchant/offers
GET /counterpilot/merchant/offers/:transaction_id
POST /counterpilot/merchant/offers/:transaction_id/accept
POST /counterpilot/merchant/offers/:transaction_id/counter
POST /counterpilot/merchant/offers/:transaction_id/decline
GET /apps/counterpilot/offers/:transaction_id/respond
POST /apps/counterpilot/offers/:transaction_id/accept
POST /counterpilot/webhooks/shopify/orders
POST /counterpilot/webhooks/shopify/refunds
POST /counterpilot/webhooks/shopify/returns
```

Submitted offers are stored in `.counterpilot-data/offers.jsonl`, which is
operational storage and is intentionally ignored by Git. The merchant inbox
routes replay the append-only event log and return current lifecycle state
without raw buyer email, raw Shopify GIDs, checkout URLs, addresses, phone
numbers, or buyer messages.

The storefront app-proxy path is buyer-side only:

```text
POST /apps/counterpilot/offers
```

Merchant inbox and action routes intentionally remain off `/apps/...` paths.
For local development they run without merchant auth by default. Set
`COUNTERPILOT_MERCHANT_AUTH_TOKEN` to require `Authorization: Bearer ...` on
merchant inbox and action requests.

When an app-proxy secret is configured with `COUNTERPILOT_SHOPIFY_API_SECRET`
or `SHOPIFY_API_SECRET`, the local server verifies Shopify app-proxy
`signature` query parameters before accepting storefront offers.

Merchant accept/counter actions return a one-time buyer response path for local
demo use. The raw token in that path is operational-only and is not persisted;
the append-only event log stores only `buyer_response_token_hash` plus
`buyer_response_expires_at`. Buyer acceptance appends `buyer_accepted`, creates
a sanitized `checkout_creation_started` marker before the external Shopify
write, creates a Shopify draft order, appends `checkout_created`, and returns
the checkout URL to the buyer response. A retry that sees a checkout started
but not finished fails closed instead of creating a duplicate draft order. Raw
checkout URLs are stored only in the gitignored
`.counterpilot-data/checkout_refs.jsonl`; `offers.jsonl` stores only hashed
draft-order and checkout references.

Shopify `orders/create` and `orders/paid` webhook deliveries are accepted at
`POST /counterpilot/webhooks/shopify/orders`. The route verifies Shopify HMAC
against the raw body before parsing JSON, deduplicates by
`X-Shopify-Webhook-Id`, ignores orders without `counterpilot_transaction_id`,
requires an existing `checkout_created` transaction, and appends sanitized
`order_created` and `paid` events. Raw Shopify order IDs and order names are
stored only in the gitignored `.counterpilot-data/order_refs.jsonl`; webhook
delivery IDs are tracked in
`.counterpilot-data/shopify_webhook_deliveries.jsonl`.

Shopify `refunds/create` webhook deliveries are accepted at
`POST /counterpilot/webhooks/shopify/refunds`. The route verifies Shopify HMAC
against the raw body, maps the refund back to a Counterpilot transaction through
`.counterpilot-data/order_refs.jsonl`, deduplicates by both delivery ID and
refund reference hash, and appends sanitized `refund_recorded` events after
`paid`. Successful refund transactions are used as financial truth first; line
item/shipping/adjustment amounts are a fallback. Missing or conflicting refund
currency is held for reconciliation instead of guessed. Raw refund, order, and
refund transaction references are stored only in the gitignored
`.counterpilot-data/refund_refs.jsonl`.

Shopify return status webhook deliveries are accepted at
`POST /counterpilot/webhooks/shopify/returns` for `returns/request`,
`returns/approve`, `returns/reopen`, `returns/close`, `returns/decline`, and
`returns/cancel`. The route verifies Shopify HMAC against the raw body, maps
the return back to a Counterpilot transaction through
`.counterpilot-data/order_refs.jsonl`, deduplicates by delivery ID and processed
return reference/status, and appends sanitized `return_status_recorded` events
after `paid`. Return events track only maturity exposure:
`returns/request`, `returns/approve`, and `returns/reopen` are `open`;
`returns/close`, `returns/decline`, and `returns/cancel` are `closed`. They do
not overwrite payment/refund lifecycle state. Raw return and order references
are stored only in the gitignored `.counterpilot-data/return_refs.jsonl`.

Counterpilot treats `offer_amount_minor`, `counter_amount_minor`, and
`accepted_amount_minor` as per-unit prices. Order-level negotiated revenue is
`accepted_amount_minor * quantity`.

Run against a development store:

```shell
shopify app dev --store <your-dev-store>.myshopify.com
```

Then:

1. Install the app in the development store.
2. Open the theme editor.
3. Go to `Products -> Default product`.
4. Add the `Make an Offer` app block.
5. Save the theme.
6. Preview a sample product page.

## Required Scopes

The current dev-store proof uses least-privilege scopes for product reads,
order reads, return status reads, and draft-order creation:

```text
read_orders,read_products,read_returns,write_draft_orders
```

Do not broaden scopes unless a specific product requirement needs it.

## Privacy Boundary

Do not commit:

- `.env`
- `.env.*`
- `.shopify/`
- access tokens
- refresh tokens
- checkout URLs
- order status URLs
- raw Shopify order IDs
- raw Shopify refund IDs
- raw Shopify refund transaction IDs
- raw Shopify return IDs
- raw Shopify return names
- return notes
- return tracking URLs
- buyer names
- buyer emails
- addresses
- phone numbers
- raw buyer messages
- `node_modules/`

The app shell `.gitignore` excludes local env files, Shopify CLI state, and
dependencies. Keep operational customer data in server-side operational storage
only; reports and research exports must stay PII-clean.

## What Not To Build Yet

Do not add these to the Shopify app shell until the server-backed golden path is
working:

- Automated counters.
- AI negotiation.
- Pricing recommendations.
- Cart-level offers.
- Multi-product bundled offers.
- Billing.
- Public App Store submission.
- Cross-merchant analytics.

The immediate job is plumbing, not intelligence: make the manual offer loop work
end to end and prove mature margin.
