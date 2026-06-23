# Counterpilot

Counterpilot is a Shopify make-an-offer app that tracks negotiated orders all
the way to mature contribution margin.

```text
shopper makes an offer
-> merchant accepts, counters, or declines
-> buyer accepts and checks out
-> Shopify order, refund, return, and maturity events are ingested
-> merchant sees whether the negotiated sale became real margin
```

The product promise is intentionally narrow:

> Put a Make an Offer button on selected products. Review offers in one inbox.
> Counter manually. Counterpilot handles checkout tracking and shows whether
> negotiated sales matured into real margin.

Counterpilot is not an AI negotiator, pricing optimizer, experimentation
platform, or generic analytics dashboard. Behavior Discovery Lab and OfferLab
remain internal research layers; merchants should only see the Shopify app,
offer inbox, buyer accept flow, and mature-margin report.

## Current Proof

The repository contains a completed Shopify development-store proof:

- Proof artifact: [`reports/counterpilot_dev_store_proof.json`](reports/counterpilot_dev_store_proof.json)
- Merchant report: [`reports/counterpilot_dev_store_report.md`](reports/counterpilot_dev_store_report.md)
- PII-clean research export: [`reports/counterpilot_dev_store_research_export.json`](reports/counterpilot_dev_store_research_export.json)

The proven dev-store lifecycle was:

```text
offer_submitted
-> merchant_countered
-> buyer_accepted
-> checkout_created
-> order_created
-> paid
-> mature
```

The proof used Shopify test payment evidence, so `production_evidence` is
correctly `false`.

## Product Surfaces

Counterpilot Private Beta v0 has four merchant-facing surfaces:

1. Setup: enable the product-page offer block, enter margin assumptions, and
   send a test offer.
2. Offer inbox: view `Product | Asking price | Buyer offer | Estimated margin |
Status | Action`.
3. Buyer accept page: show the counter amount and create checkout only after
   buyer acceptance.
4. Mature-margin report: show negotiated revenue after cost basis, shipping,
   fees, refunds, returns, and maturity.

In scope for the first sellable version:

- Product-page offers for one product at a time.
- Merchant manual accept, counter, and decline.
- Buyer acceptance and Shopify draft-order checkout.
- Paid/refund/return webhook ingestion.
- Refund-aware maturity window.
- Merchant mature-margin report.
- PII-clean report/export/proof guards.
- Demo mode.

Out of scope for now:

- Automated counters.
- ML or recommendation models.
- Seller-side experiments.
- Billing.
- Public App Store distribution.
- Multi-merchant learning.
- Cart-level or bundled offers.
- Buyer chat or raw buyer messages.
- Causal lift claims.

## Repository Map

```text
apps/shopify/counterpilot-dev/
  Shopify CLI app shell and Counterpilot theme app extension

integrations/shopify/
  Shopify adapter, dev-store checks, provider boundary, webhook validation

src/behavior_lab/counterpilot*.py
src/behavior_lab/counterpilot_state/
src/behavior_lab/counterpilot_storage/
  Counterpilot transaction ledger, state machine, reporting, and storage

reports/
  Committed dev-store proof, report, and redacted research export

docs/
  Product, runbook, privacy, demo, acceptance-test, and internal research docs
```

Start with:

- [`docs/COUNTERPILOT_PRODUCT.md`](docs/COUNTERPILOT_PRODUCT.md)
- [`docs/COUNTERPILOT_RUNBOOK.md`](docs/COUNTERPILOT_RUNBOOK.md)
- [`docs/COUNTERPILOT_ACCEPTANCE_TEST.md`](docs/COUNTERPILOT_ACCEPTANCE_TEST.md)
- [`docs/COUNTERPILOT_PRIVACY_BOUNDARIES.md`](docs/COUNTERPILOT_PRIVACY_BOUNDARIES.md)
- [`docs/COUNTERPILOT_DEMO_SCRIPT.md`](docs/COUNTERPILOT_DEMO_SCRIPT.md)

## Quick Start

Install and test the Python package:

```powershell
python -m pip install -e .
python -m pytest tests/shopify tests/test_counterpilot_reports.py -q
python -m compileall -q src tests integrations tools
```

Build the Shopify app shell:

```powershell
cd apps\shopify\counterpilot-dev
npm install
npm run build
```

Run against a Shopify development store:

```powershell
shopify app dev --store <your-dev-store>.myshopify.com
```

The app shell contains the current product-page `Make an Offer` theme app
extension plus a local server-backed loop for offer submission, merchant
actions, buyer acceptance, Shopify draft-order checkout creation, paid order and
refund webhook ingestion, and return exposure tracking. The next product
milestone is maturity jobs and reporting through that same server-backed path.

## Golden Acceptance Test

Every meaningful build should pass this product contract:

```text
buyer submits product-page offer
-> Counterpilot persists offer
-> merchant sees offer in inbox
-> merchant counters manually
-> buyer accepts counter
-> Counterpilot creates draft order / checkout flow
-> paid order webhook is ingested
-> refund event is handled if present
-> return exposure blocks maturity if open
-> maturity job closes the lifecycle
-> merchant report is generated
-> report contains no raw Shopify IDs, checkout URLs, tokens, names, emails,
   addresses, phone numbers, or raw buyer messages
-> dev/test runs are marked production_evidence=false
```

Anything that does not help this test pass should be deferred.

## Internal Research Layers

Behavior Discovery Lab, OfferLab, benchmark lanes, NBER tooling, and lockbox
evaluation remain in the repository as internal infrastructure and research
history. They are not the merchant-facing product entrance.

Useful internal docs:

- [`docs/COUNTERPILOT.md`](docs/COUNTERPILOT.md)
- [`docs/OFFERLAB.md`](docs/OFFERLAB.md)
- [`docs/DATASET_ROADMAP.md`](docs/DATASET_ROADMAP.md)
- [`docs/WAVE_AUDIT_PROTOCOL.md`](docs/WAVE_AUDIT_PROTOCOL.md)

Keep the product boring, narrow, and obviously money-linked.
