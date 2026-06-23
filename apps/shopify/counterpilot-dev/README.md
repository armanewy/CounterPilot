# Counterpilot Shopify App Shell

This directory contains the Shopify CLI app shell for Counterpilot.

Counterpilot is a Shopify make-an-offer app with one golden path:

```text
shopper submits product-page offer
-> merchant reviews in Counterpilot inbox
-> merchant accepts, counters, or declines
-> buyer accepts counter
-> Shopify draft order / checkout is created
-> paid/refund webhooks are ingested
-> maturity window closes
-> merchant sees true mature margin
```

This app shell currently contains the product-page theme app extension used in
the development-store proof. It is not the complete production app server yet.
The next implementation step is a backend for offer submission, merchant
actions, buyer accept pages, draft order creation, webhook ingest, maturity
jobs, and report generation.

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
order reads, and draft-order creation:

```text
read_orders,read_products,write_draft_orders
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
