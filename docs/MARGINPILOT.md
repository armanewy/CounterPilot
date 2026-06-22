# MarginPilot

MarginPilot is the commercial product boundary for OfferLab:

```text
merchant-controlled offer or quote surface
-> append-only economic ledger
-> consent-gated shadow recommendations
-> randomized policy experiments
-> guarded automation only after verified mature-margin lift
```

The product optimizes mature contribution margin, not acceptance rate, GMV, or
sale price alone. Behavior Discovery Lab remains the internal research engine;
customers should not see model leaderboards, NBER artifacts, or lockboxes.

## Current Implementation

This repository now implements the Month 1 transaction-surface foundation:

- `marginpilot-template`
- `marginpilot-ingest`
- `marginpilot-inbox`
- `marginpilot-audit`
- `marginpilot-transaction-create`
- `marginpilot-event-append`
- `marginpilot-transaction-inspect`
- `marginpilot-consent-grant`
- `marginpilot-consent-revoke`
- `marginpilot-research-export`
- `marginpilot-run-local-fixture`

The event ledger supports:

- explicit merchant-specific learning consent
- product-page, cart, quote, or merchant-entered offers
- merchant accept/counter/decline/wait decisions
- randomized assignment metadata for future experiments
- mature contribution-margin outcomes after returns/cancellations

The inbox is accounting-only. It computes mature margin if sold for available
actions and marks merchant-floor violations. It does not execute seller actions
and does not train models.

The transaction core records one negotiated commerce loop through the
deterministic state machine documented in `docs/MARGINPILOT_STATE_MACHINE.md`.
Operational customer data lives behind the encrypted operational adapter
documented in `docs/MARGINPILOT_DATA_BOUNDARIES.md`; research exports contain
pseudonymous identifiers, economic fields, consent lineage, and dataset
lineage, not names, emails, addresses, phone numbers, checkout URLs, or
Shopify resource IDs.

## Data Rights

MarginPilot requires explicit merchant consent before merchant-specific
learning is treated as authorized. Cross-merchant pooling is off by default.
Customer names, emails, addresses, phone numbers, IP addresses, and raw buyer or
customer identifiers are rejected from event payloads.

Operational data and training-authorized data must remain separable. The audit
reports whether merchant-specific learning is authorized and whether the data is
ready for a shadow optimizer.

## Commands

```powershell
python -m behavior_lab marginpilot-template --output-dir C:\OfferLabData\marginpilot_templates
python -m behavior_lab marginpilot-ingest --input C:\OfferLabData\marginpilot_templates\merchant_consent.json
python -m behavior_lab marginpilot-ingest --input C:\OfferLabData\marginpilot_templates\offer_opened.json
python -m behavior_lab marginpilot-inbox --merchant-id merchant_demo_refurb_tech
python -m behavior_lab marginpilot-audit --merchant-id merchant_demo_refurb_tech
python -m behavior_lab marginpilot-run-local-fixture --data-dir C:\OfferLabData\marginpilot_core
python -m behavior_lab marginpilot-transaction-inspect --data-dir C:\OfferLabData\marginpilot_core --merchant-namespace merchant_demo_refurb:store_demo_shopify --transaction-id txn_marginpilot_loop_001
python -m behavior_lab marginpilot-research-export --data-dir C:\OfferLabData\marginpilot_core --merchant-id merchant_demo_refurb --store-id store_demo_shopify
```

## Gates

The profit-optimization gate currently requires:

- merchant-specific learning consent
- at least 80% cost-basis coverage
- at least 30 mature paid outcomes
- no customer PII in model/event features

Until those pass, the correct stage is `transaction_surface`, not automation.
