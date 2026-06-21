# OfferLab

OfferLab is the commercial direction for Behavior Discovery Lab.

The lab remains the internal engine. The product surface is a seller-side decision tool:

```text
Connect eBay data
→ record immutable offer/listing decisions
→ produce a read-only profit audit
→ abstain or recommend with explicit evidence limits
→ later run guarded experiments
```

## Stages

1. Read-only profit audit.
2. Decision support for incoming offers.
3. Controlled seller-initiated offer experiments at the listing level.
4. Guarded automation only after prospective evidence.

## Current Implementation

This repo now implements Stage 1 scaffolding:

- `campaign_002_ebay_seller_offers`
- normalized decision snapshot validation
- append-only historical ingest
- realized mature-margin audit
- five-section profit-audit report
- read-only recommendation arithmetic with abstention gates
- adapter interface with no eBay mutation methods

The recommendation source is deliberately labeled `deterministic_read_only_arithmetic_v2`. It is useful arithmetic, not a learned negotiation model. When the data is not good enough, the correct output is `abstain`.

## Profit Audit Product

The first sellable deliverable is a report, not automation:

```powershell
python -m behavior_lab offerlab-ingest --input campaigns/campaign_002_ebay_seller_offers/examples/historical_decisions.jsonl
python -m behavior_lab offerlab-report --output reports/offerlab_profit_audit.md
```

The report contains:

1. Historical policy audit.
2. Profit frontier.
3. Missed-opportunity candidates.
4. Proposed guarded policy.
5. Prospective test plan.

It also includes a data-quality score based on seller cost-basis coverage, actual eBay fee coverage, mature return-window coverage, and traffic freshness.

## Integrity Rules

- `seller_cost_basis` may be `null`, but recommendations must abstain when it is missing.
- `seller_accepted` and `buyer_paid` are separate outcomes. An accepted offer is not revenue until the buyer pays.
- Profit scoring uses `mature_margin` after the return window matures. `provisional_margin` is informational only.
- Buyer-originated Best Offers, seller-initiated offers, counteroffer policies, listing price reductions, and passive wait policies must not be pooled.
- Stale traffic data and insufficient comparable mature outcomes force abstention.
- Retrospective comparisons are hypotheses for a future randomized test, not causal proof.

## eBay API Notes

Use official APIs only. As of the checked docs, relevant surfaces are:

- Trading API `GetBestOffers` and `RespondToBestOffer`
- Sell Negotiation API for offers to interested buyers
- Sell Analytics API traffic reports
- Sell Finances transactions for actual fees and seller financial events
- seller order APIs for completed outcomes, unpaid orders, cancellations, and returns

Implementation should keep these behind adapters so platform changes do not contaminate the experiment ledger.

Do not build a real connector until there is a seller pilot, cost-basis process, and explicit OAuth authorization plan.

## Managed Service Wedge

Do not build SaaS first. Sell one managed experiment:

```text
I analyze your listings, offers, and sales, then run one controlled pricing or offer experiment designed to increase realized margin. You approve every action.
```

Target sellers with repeatable inventory, known cost basis, enough active listings, and meaningful monthly transaction volume.
