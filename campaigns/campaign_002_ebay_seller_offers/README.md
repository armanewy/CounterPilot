# Campaign 002 - eBay Seller Offers

Campaign 002 turns the lab toward a commercial wedge: seller-side offer and negotiation policy optimization for eBay.

The first stage is read-only. It ingests normalized snapshots of listings, offers, traffic, seller costs, actions, and outcomes. It recommends actions through transparent economics, but it does not call eBay or execute marketplace actions.

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

Recommend for one pending offer:

```powershell
python -m behavior_lab offerlab-recommend --input campaigns/campaign_002_ebay_seller_offers/examples/pending_offer_snapshot.json
```

## Read-Only Adapter Boundary

Future eBay integration should use official APIs only:

- Trading API for Best Offer retrieval and, later, seller-approved responses.
- Sell Negotiation API for seller-initiated offers to interested buyers.
- Sell Analytics API traffic reports for impressions, views, conversion, and completed transactions.
- Transaction/order feeds for completed sales, unpaid orders, returns, and final prices.

No adapter may mutate eBay state during Stage 1.

## Product Rule

Do not optimize acceptance rate. Optimize contribution margin under uncertainty. Discounting everything is not success.

The customer-facing output should be:

```text
Accept
Counter
Wait
```

with expected dollars, uncertainty, and floor violations.
