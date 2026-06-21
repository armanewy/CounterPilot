# OfferLab

OfferLab is the commercial direction for Behavior Discovery Lab.

The lab remains the internal engine. The product surface is a seller-side decision tool:

```text
Connect eBay data
→ record immutable offer/listing decisions
→ estimate expected contribution margin for available actions
→ let the seller approve action
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
- realized margin audit
- read-only recommendation arithmetic
- adapter interface with no eBay mutation methods

The recommendation source is deliberately labeled `deterministic_read_only_arithmetic_v1`. It is useful arithmetic, not a learned negotiation model.

## eBay API Notes

Use official APIs only. As of the checked docs, relevant surfaces are:

- Trading API `GetBestOffers` and `RespondToBestOffer`
- Sell Negotiation API for offers to interested buyers
- Sell Analytics API traffic reports
- seller transaction/order APIs for completed outcomes

Implementation should keep these behind adapters so platform changes do not contaminate the experiment ledger.

## Managed Service Wedge

Do not build SaaS first. Sell one managed experiment:

```text
I analyze your listings, offers, and sales, then run one controlled pricing or offer experiment designed to increase realized margin. You approve every action.
```

Target sellers with repeatable inventory, known cost basis, enough active listings, and meaningful monthly transaction volume.
