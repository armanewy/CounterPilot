# Counterpilot Runbook

This runbook is for building and demoing the Shopify make-an-offer product
loop. It is not a research benchmark runbook.

## Prerequisites

- Python 3.11 or newer.
- Node.js 22.12 or newer.
- npm.
- Git.
- Shopify CLI.
- Shopify Partner account.
- Shopify development store.

## Python Setup

From the repository root:

```powershell
python -m pip install -e .
python -m pytest tests/shopify tests/test_counterpilot_reports.py -q
python -m compileall -q src tests integrations tools
```

## Shopify App Shell

From the repository root:

```powershell
cd apps\shopify\counterpilot-dev
npm install
npm run build
shopify app dev --store <your-dev-store>.myshopify.com
```

The Shopify CLI prints links for app install, theme editor setup, and preview.

Use the theme editor to add the `Make an Offer` app block to:

```text
Products -> Default product
```

Keep the cart offer block disabled for Private Beta v0.

## Dev-Store Configuration Check

The non-mutating checker should fail closed until all env vars are configured:

```powershell
python -m behavior_lab counterpilot-devstore-check
```

Required environment variables:

```powershell
$env:COUNTERPILOT_SHOPIFY_STORE_MODE="development"
$env:COUNTERPILOT_SHOPIFY_STORE_DOMAIN="<store>.myshopify.com"
$env:COUNTERPILOT_SHOPIFY_ACCESS_TOKEN="..."
$env:COUNTERPILOT_SHOPIFY_WEBHOOK_SECRET="..."
$env:COUNTERPILOT_SHOPIFY_APP_URL="https://..."
$env:COUNTERPILOT_SHOPIFY_SCOPES="read_orders,read_products,read_returns,write_draft_orders"
$env:COUNTERPILOT_SHOPIFY_PROVIDER_MODE="real"
$env:COUNTERPILOT_MERCHANT_ID="merchant_dev_demo"
$env:COUNTERPILOT_STORE_ID="store_dev_shopify"
$env:COUNTERPILOT_DATA_DIR="C:\OfferLabData\counterpilot_devstore"
$env:COUNTERPILOT_SERVER_DATA_DIR=$env:COUNTERPILOT_DATA_DIR
```

The app URL must expose these webhook paths:

```text
/counterpilot/webhooks/shopify/orders
/counterpilot/webhooks/shopify/refunds
/counterpilot/webhooks/shopify/returns
```

Never commit env files or token values.

## Existing Dev-Store Proof

The committed proof artifacts are:

```text
reports/counterpilot_dev_store_proof.json
reports/counterpilot_dev_store_report.md
reports/counterpilot_dev_store_research_export.json
```

The proven lifecycle was:

```text
offer_submitted
-> merchant_countered
-> buyer_accepted
-> checkout_created
-> order_created
-> paid
-> mature
```

The proof used a Shopify development store and test payment, so
`production_evidence` is `false`.

## Current Server-Backed Loop

The app shell includes backend routes for:

- Offer submission from the product-page block.
- Merchant inbox data.
- Merchant accept/counter/decline actions.
- Buyer accept page.
- Draft order / checkout creation.
- Shopify `orders/create` and `orders/paid` webhook ingest.
- Shopify `refunds/create` webhook ingest.
- Shopify return status webhook ingest for maturity exposure.
- CLI maturity job.

The remaining implementation needs backend routes or jobs for:

- Report generation.

Use Shopify-native rails where possible:

- Theme app extension for storefront surface.
- App proxy route for storefront-to-backend requests.
- Admin GraphQL for draft orders.
- Webhooks for order/refund/return/app lifecycle.

## Maturity Job

Create an explicit gitignored margin config before running maturity:

```json
{
  "schema_version": "counterpilot.margin_config.v1",
  "maturity_window_days": 0,
  "default_product_cost_minor": 42000,
  "default_shipping_cost_minor": 3500,
  "default_platform_fee_minor": 0,
  "default_return_loss_minor": 0,
  "currency": "USD"
}
```

Save it at:

```text
.counterpilot-data/margin_config.json
```

Then run:

```powershell
cd apps\shopify\counterpilot-dev
npm run counterpilot:mature
```

The job appends sanitized `mature` events only for paid negotiated
transactions whose maturity window has closed, whose refund currency matches
the config, and whose return exposure and reconciliation state are closed. It
does not expose raw Shopify IDs, checkout URLs, tokens, buyer contact fields,
or raw buyer messages.

## Demo Checklist

Before showing the product to another person:

1. `npm run build` passes in `apps/shopify/counterpilot-dev`.
2. Shopify app installs on a dev store.
3. Product-page `Make an Offer` block renders.
4. Buyer can submit an offer.
5. Merchant sees it in the inbox.
6. Merchant counters.
7. Buyer accepts.
8. Draft order / checkout is created.
9. Test payment completes.
10. Order paid webhook is ingested.
11. Refund handling is either exercised or explicitly skipped with reason.
12. Return exposure handling is either exercised or explicitly skipped with
    reason.
13. Maturity is recorded with `npm run counterpilot:mature`.
14. Merchant report is generated.
15. Report/proof/export pass PII scans.

## Troubleshooting

If `shopify app dev` asks for the store password, use the Online Store password
from the Shopify admin, not the account password.

If Shopify reports missing scopes, remove generated template features before
broadening scopes. Counterpilot Private Beta v0 should need only:

```text
read_orders,read_products,read_returns,write_draft_orders
```

If the theme block does not appear, rebuild the app shell and confirm the
`counterpilot-offer-surface` theme app extension is listed by Shopify CLI.

If reports contain raw Shopify IDs, checkout URLs, tokens, or buyer contact
fields, treat that as a blocking privacy failure.
