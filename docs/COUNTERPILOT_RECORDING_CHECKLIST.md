# Counterpilot Recording Checklist

Use this before recording the private-beta demo.

## Local Or Dev-Store Prep

- Confirm `npm run build` passes in `apps/shopify/counterpilot-dev`.
- Confirm the Shopify development store opens cleanly.
- Confirm the app is installed in the development store.
- Confirm the product-page `Make an Offer` theme block is enabled.
- Confirm demo data is synthetic or development-store safe.
- Close any terminal panes showing secrets or raw operational data.

## Margin Config

Create `.counterpilot-data/margin_config.json` in the app directory or set
`COUNTERPILOT_SERVER_DATA_DIR` to the intended local data directory.

Use explicit demo assumptions:

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

## Recording Flow

1. Show the product page with the `Make an Offer` block.
2. Submit an offer.
3. Show the merchant inbox row.
4. Merchant counters manually.
5. Show the buyer accept page.
6. Buyer accepts.
7. Show that Shopify checkout is created.
8. Show paid webhook or test-payment evidence only if it is sanitized.
9. If using refund or return evidence, keep it sanitized.
10. Run `npm run counterpilot:mature`.
11. Run `npm run counterpilot:report`.
12. Show the generated merchant report.

## Privacy Scan

Before sharing the recording or sample report:

- Check that the report says `production_evidence: false` or equivalent dev
  evidence language.
- Confirm no buyer contact data appears.
- Confirm no raw Shopify identifiers appear.
- Confirm no checkout or order status links appear.
- Confirm no token or secret appears in terminal output.

## Do Not Show On Screen

- Access tokens.
- Refresh tokens.
- App secrets.
- Webhook secrets.
- Checkout URLs in logs.
- Order status URLs.
- Customer names.
- Customer emails.
- Addresses.
- Phone numbers.
- Raw Shopify identifiers.
- Raw webhook payloads.
- Raw buyer messages.

## Final Clip Shape

Keep the recording to about 30 seconds. The intended viewer should leave with
one idea:

```text
Counterpilot lets me run manual negotiated offers and see observed mature
margin after payment, refunds, and returns.
```
