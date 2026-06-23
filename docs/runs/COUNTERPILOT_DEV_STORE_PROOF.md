# Counterpilot Dev-Store Proof Checklist

This checklist is the live Shopify development-store gate. It must be completed
before adding recommendations, automated counters, experiments, billing, or
public app-store distribution.

## Preflight

1. Create or select a Shopify development store.
2. Create a development app with least-privilege scopes:
   - `read_orders`
   - `read_products`
   - `write_draft_orders`
3. Configure HTTPS app and webhook URLs.
4. Configure required environment variables:

```powershell
$env:COUNTERPILOT_SHOPIFY_STORE_MODE="development"
$env:COUNTERPILOT_SHOPIFY_STORE_DOMAIN="your-dev-store.myshopify.com"
$env:COUNTERPILOT_SHOPIFY_ACCESS_TOKEN="..."
$env:COUNTERPILOT_SHOPIFY_WEBHOOK_SECRET="..."
$env:COUNTERPILOT_SHOPIFY_APP_URL="https://..."
$env:COUNTERPILOT_SHOPIFY_WEBHOOK_URL="https://.../webhooks/shopify"
$env:COUNTERPILOT_SHOPIFY_SCOPES="read_orders,read_products,write_draft_orders"
$env:COUNTERPILOT_SHOPIFY_PROVIDER_MODE="real"
$env:COUNTERPILOT_MERCHANT_ID="merchant_dev_demo"
$env:COUNTERPILOT_STORE_ID="store_dev_shopify"
$env:COUNTERPILOT_DATA_DIR="C:\OfferLabData\counterpilot_devstore"
```

5. Run the non-mutating configuration check:

```powershell
python -m behavior_lab counterpilot-devstore-check
```

The command must not print access tokens or raw customer data.

## Store Setup

1. Install the development app.
2. Enable the product-page Counterpilot `Make an Offer` theme app block.
3. Create one test product with one variant.
4. Record:
   - product GID
   - variant GID
   - SKU
   - asking price
   - cost basis
   - merchant floor
   - shipping and fulfillment assumptions
5. Confirm the cart-level offer block remains disabled unless explicitly under
   test.

## Transaction Loop

1. Buyer submits an offer from the product page.
2. Confirm the merchant inbox shows the offer.
3. Merchant counters with an explicit amount.
4. Buyer accepts the counter.
5. Counterpilot creates a Shopify draft order or supported checkout equivalent.
6. Confirm the checkout/invoice link is delivered through operational storage
   references only.
7. Complete a Shopify test payment.
8. Ingest `orders/create` or `orders/created`.
9. Ingest `orders/updated` or `orders/paid`.
10. Issue or simulate a refund/return event where supported.
11. Ingest `refunds/create` or `refunds/created`.
12. Ingest return status if Shopify exposes it for the dev-store flow.
13. Mark the outcome mature only after the refund/return window is complete or
    deliberately shortened for the development proof.

## Report And Export

Run:

```powershell
python -m behavior_lab counterpilot-report --data-dir $env:COUNTERPILOT_DATA_DIR --format markdown --output reports\counterpilot_dev_store_report.md
python -m behavior_lab counterpilot-research-export --data-dir $env:COUNTERPILOT_DATA_DIR --merchant-id $env:COUNTERPILOT_MERCHANT_ID --store-id $env:COUNTERPILOT_STORE_ID
```

Verify:

- the offer funnel includes the live transaction
- mature margin reconciles exactly
- shipping discounts and refunds are visible
- missing cost basis, fees, or maturity marks totals provisional
- research export contains no names, emails, addresses, phone numbers, raw buyer
  messages, checkout URLs, access tokens, or raw Shopify resource IDs

## Redacted Proof Artifact

When the run completes, save:

```text
reports/counterpilot_dev_store_proof.json
```

Required fields:

- app version
- git commit
- store mode `development`
- timestamp
- transaction ID
- event IDs
- state transition sequence
- Shopify resource hashes derived from raw IDs
- final mature margin components
- report hash
- research export hash
- PII scan result
- manual steps completed
- skipped steps with reasons

Forbidden fields:

- access tokens
- refresh tokens
- customer names
- emails
- addresses
- phone numbers
- raw buyer message text
- checkout URLs

## Pass Criteria

The proof passes only when one real dev-store buyer offer creates a paid test
order, Counterpilot ingests the lifecycle, mature contribution margin
reconciles, and the merchant report is useful enough to show in a demo.
