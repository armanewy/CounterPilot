# NBER Best Offer Benchmark Audit

The NBER lane is the primary public evidence campaign for OfferLab. It must answer whether observable bargaining variables predict seller actions, buyer responses, agreement, final price ratio, and response latency under leakage-safe splits.

## Required Controls

- Chronological split.
- Seller-disjoint split where seller identifiers are available.
- Category breakdown.
- Future-round leakage check.
- Final-price and final-status leakage check.
- Random-label control before accepting any complex model.
- Identifier memorization check before using buyer or seller history.

## Current Implementation

The repository implements a fixture-sized NBER path:

- `nber-best-offer build-sample`
- `nber-best-offer normalize`
- `nber-best-offer benchmark`
- `nber-best-offer audit`

The normalizer emits JSONL partition tables as a standard-library fallback. Full-scale parquet output should be added only with an optional dependency group after the acquisition path is exercised on real files.

## Interpretation

This benchmark can support a statement like:

> Observable Best Offer variables predict negotiation outcomes better than simple baselines under chronological and seller-disjoint tests.

It cannot support:

> OfferLab causally increases seller profit.

That requires seller cost basis, actual fees, returns, holding costs, and prospective randomized or shadow-mode evidence.
