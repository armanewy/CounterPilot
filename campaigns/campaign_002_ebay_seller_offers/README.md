# Campaign 002 - eBay Seller Offers

Campaign 002 turns the lab toward a commercial wedge: seller-side offer and negotiation policy optimization for eBay.

The first stage is read-only. It ingests normalized snapshots of listings, offers, traffic, seller costs, actions, and outcomes. The primary output is a profit-audit report. Recommendations are allowed only when the evidence gate passes; otherwise the correct output is `abstain`.

## Current Commands

Write a normalized snapshot template:

```powershell
python -m behavior_lab offerlab-template
```

Ingest normalized historical snapshots:

```powershell
python -m behavior_lab offerlab-ingest --input campaigns/campaign_002_ebay_seller_offers/examples/historical_decisions.jsonl
```

Audit realized margin:

```powershell
python -m behavior_lab offerlab-audit
```

Write the managed-service report:

```powershell
python -m behavior_lab offerlab-report --output reports/offerlab_profit_audit.md
```

Recommend for one pending offer:

```powershell
python -m behavior_lab offerlab-recommend --input campaigns/campaign_002_ebay_seller_offers/examples/pending_offer_snapshot.json
```

With the bundled toy data, recommendation should abstain because there are too few comparable mature outcomes. That is intentional.

## Read-Only Adapter Boundary

Future eBay integration should use official APIs only:

- Trading API for Best Offer retrieval and, later, seller-approved responses.
- Sell Negotiation API for seller-initiated offers to interested buyers.
- Sell Analytics API traffic reports for impressions, views, conversion, and completed transactions.
- Sell Finances transactions for actual fee fields and other financial events.
- Transaction/order feeds for completed sales, unpaid orders, returns, and final prices.

No adapter may mutate eBay state during Stage 1. Do not build the real connector until there is a seller pilot and explicit OAuth authorization plan.

## Product Rule

Do not optimize acceptance rate. Optimize contribution margin under uncertainty. Discounting everything is not success.

The customer-facing output should be:

```text
Accept
Counter
Wait
```

with expected dollars, uncertainty, floor violations, and abstention reasons when the data is not strong enough.
