# Counterpilot Product

Counterpilot is a Shopify make-an-offer app for negotiated commerce margin
truth.

The product helps a merchant answer one practical question:

> Did the negotiated order actually make money after cost basis, shipping,
> fees, refunds, returns, and maturity?

It should not be presented as a research platform, AI negotiator, generic
experimentation system, or conversion dashboard.

## Pitch

Put a Make an Offer button on selected Shopify products. Review offers in one
inbox. Counter manually. Counterpilot handles checkout tracking and shows
whether negotiated sales matured into real margin.

## Golden Path

```text
shopper submits product-page offer
-> Counterpilot persists the offer
-> merchant reviews offer in inbox
-> merchant accepts, counters, or declines
-> buyer accepts counter
-> Counterpilot creates Shopify draft order / checkout flow
-> paid order webhook is ingested
-> refund/return webhook is ingested if present
-> maturity window closes the lifecycle
-> merchant sees mature-margin report
```

## Merchant Surfaces

### 1. Setup

Setup should stay short:

1. Enable Counterpilot on selected products or one collection.
2. Enter margin assumptions:
   - cost basis
   - default shipping or fulfillment cost
   - return maturity days
   - fee assumptions
3. Add the product-page theme block.
4. Send a test offer.

The merchant should finish setup knowing where offers appear, how to respond,
and why the mature-margin report matters.

### 2. Offer Inbox

The inbox is the center of the product.

Each row should show:

```text
Product | Asking price | Buyer offer | Estimated margin | Status | Action
```

Allowed actions for Private Beta v0:

- Accept
- Counter
- Decline

Avoid recommendation, AI, optimizer, auto-counter, and experiment language.
The merchant chooses every action.

### 3. Buyer Acceptance Page

When the merchant counters, the buyer sees a simple page:

```text
Your offer was countered at $X.
Accept and checkout.
```

Only after buyer acceptance should Counterpilot create the Shopify draft order
or checkout flow.

### 4. Mature-Margin Report

The report should answer:

```text
Did negotiated orders become real margin?
```

It should show negotiated revenue, item cost, shipping and fulfillment,
free-shipping cost, platform/payment fees, refunds, return loss, maturity, and
mature contribution margin.

It must not claim causal lift or conversion improvement.

## Private Beta v0 Scope

Includes:

- Product-page offer block.
- Buyer offer submission.
- Merchant inbox.
- Manual accept, counter, decline.
- Buyer accept page.
- Draft order / checkout creation.
- Paid order webhook ingestion.
- Refund webhook ingestion or manual refund reconciliation fallback.
- Maturity window.
- Merchant mature-margin report.
- PII-clean export/report guard.
- Demo mode.

Excludes:

- Automated negotiation.
- ML.
- Billing.
- Public App Store submission.
- Multi-merchant learning.
- Causal attribution.
- Generic analytics dashboards.
- Complex customization.
- Cart-level offers.
- Multi-product bundled offers.
- Buyer chat or free-text negotiation.

## Differentiation

Counterpilot should not compete as another offer button. The wedge is:

```text
Negotiated-commerce margin truth.
```

Most make-an-offer tools emphasize offer capture, automation, and conversion.
Counterpilot should emphasize whether negotiated orders survived payment,
refund, return, fee, cost, and maturity accounting.

## Pricing Hypothesis

Private pilot pricing should be simple:

- Flat pilot fee: `$99-$299/month`.
- Or a small percentage of Counterpilot-mediated paid negotiated orders.
- Or a later hybrid: `$49/month + 1% of Counterpilot-mediated paid negotiated
  orders`.

Do not charge on claimed profit lift yet. The reports are intentionally
non-causal, so pricing should attach to clearly mediated transactions or a flat
pilot fee.
