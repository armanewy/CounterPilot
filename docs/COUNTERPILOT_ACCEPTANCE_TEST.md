# Counterpilot Acceptance Test

This is the product contract. A build is not demo-ready until this lifecycle
passes end to end.

## Golden Path

1. Buyer submits a product-page offer.
2. Counterpilot persists the offer.
3. Merchant sees the offer in the inbox.
4. Merchant counters manually.
5. Buyer accepts the counter.
6. Counterpilot creates a draft order / checkout flow.
7. Order paid webhook is ingested.
8. Refund event is handled if present.
9. Maturity job closes the lifecycle.
10. Merchant report is generated.
11. Report contains no raw Shopify GIDs, checkout URLs, tokens, names, emails,
    addresses, phone numbers, or raw buyer messages.
12. Dev/test runs are marked `production_evidence: false`.

## Required State Sequence

At minimum, a successful negotiated checkout should produce:

```text
offer_submitted
-> merchant_countered
-> buyer_accepted
-> checkout_created
-> order_created
-> paid
-> mature
```

If a refund or return occurs, the sequence should include the appropriate
refund/return state before `mature`.

## Required Report Content

The merchant report must include:

- offer funnel
- mature margin summary
- margin leakage
- product/SKU or product grouping
- refund/return impact
- explicit non-causal language
- explicit indication that it is not a recommendation model

## Required Privacy Scan

The report, research export, and proof artifact must not contain:

- raw Shopify GIDs
- checkout URLs
- access tokens
- refresh tokens
- customer names
- customer emails
- addresses
- phone numbers
- IP addresses
- raw buyer messages

## Existing Passing Dev Proof

The committed development-store proof passed the lifecycle through `mature`:

```text
reports/counterpilot_dev_store_proof.json
```

It used Shopify test payment evidence, so `production_evidence` is `false`.

## Blocking Failures

Treat any of these as blockers:

- offer submission is not persisted
- checkout is created before buyer acceptance
- paid webhook is not bound to the transaction
- mature margin does not reconcile
- report hides refunds or free-shipping costs
- dev/test evidence is treated as production evidence
- report/export/proof contains operational identifiers or PII
- app requires automated negotiation to be useful
