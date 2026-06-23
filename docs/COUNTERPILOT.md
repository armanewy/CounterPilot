# Counterpilot

Counterpilot is the commercial product boundary for OfferLab:

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

## Rename Note

Counterpilot was previously called MarginPilot internally during prototyping.
The product-facing layer was renamed to avoid a name collision. The
`marginpilot-*` CLI commands remain as deprecated aliases for one transition
release; new docs and examples use `counterpilot-*`.

## Current Implementation

This repository now implements the Month 1 transaction-surface foundation:

- `counterpilot-template`
- `counterpilot-ingest`
- `counterpilot-inbox`
- `counterpilot-audit`
- `counterpilot-utility-report`
- `counterpilot-rule-sim`
- `counterpilot-shadow-recommend`
- `counterpilot-experiment`
- `counterpilot-transaction-create`
- `counterpilot-event-append`
- `counterpilot-transaction-inspect`
- `counterpilot-consent-grant`
- `counterpilot-consent-revoke`
- `counterpilot-research-export`
- `counterpilot-run-local-fixture`

The event ledger supports:

- explicit merchant-specific learning consent
- product-page, cart, quote, or merchant-entered offers
- merchant accept/counter/decline/wait decisions
- randomized assignment metadata for future experiments
- mature contribution-margin outcomes after returns/cancellations

The inbox is accounting-only. It computes mature margin if sold for available
actions and marks merchant-floor violations. It does not execute seller actions
and does not train models.

The Wave 3 utility report is also non-learning. It summarizes offer volume,
acceptance funnel, mature margin per accepted offer, margin by product and
inventory age, amount conceded versus asking price, time from offer to payment,
unpaid accepted offers, and refund/return-adjusted margin. The fixed-rule
simulator answers "what would this simple rule have selected historically?"
using observed contexts only. It is not a causal estimate.

Wave 4 adds shadow recommendations from transparent merchant rules only. A
shadow recommendation is appended before the merchant decision, records the
available actions and evidence, and never executes a seller action. It abstains
when cost basis is missing, comparable mature outcomes are too sparse, traffic
is stale, return maturity is incomplete, or customer-targeting features such as
location proxies are present.

Wave 5 adds controlled experiment records. The first supported design compares
ordinary merchant response against showing a Counterpilot shadow recommendation,
with merchant adoption as the primary outcome. The second compares a fixed
merchant counter rule against a Counterpilot counter rule at the listing or
negotiation-session level, with mature contribution margin per eligible
negotiation as the primary outcome. Offer-policy experiments require guardrails
for minimum net floor, maximum concession, persistent holdout, and no
customer-level sensitive targeting.

The transaction core records one negotiated commerce loop through the
deterministic state machine documented in `docs/COUNTERPILOT_STATE_MACHINE.md`.
Operational customer data lives behind the encrypted operational adapter
documented in `docs/COUNTERPILOT_DATA_BOUNDARIES.md`; research exports contain
pseudonymous identifiers, economic fields, consent lineage, and dataset
lineage, not names, emails, addresses, phone numbers, checkout URLs, or
Shopify resource IDs.

## Data Rights

Counterpilot requires explicit merchant consent before merchant-specific
learning is treated as authorized. Cross-merchant pooling is off by default.
Customer names, emails, addresses, phone numbers, IP addresses, and raw buyer or
customer identifiers are rejected from event payloads.

Operational data and training-authorized data must remain separable. The audit
reports whether merchant-specific learning is authorized and whether the data is
ready for a shadow optimizer.

## Commands

```powershell
python -m behavior_lab counterpilot-template --output-dir C:\OfferLabData\counterpilot_templates
python -m behavior_lab counterpilot-ingest --input C:\OfferLabData\counterpilot_templates\merchant_consent.json
python -m behavior_lab counterpilot-ingest --input C:\OfferLabData\counterpilot_templates\offer_opened.json
python -m behavior_lab counterpilot-inbox --merchant-id merchant_demo_refurb_tech
python -m behavior_lab counterpilot-audit --merchant-id merchant_demo_refurb_tech
python -m behavior_lab counterpilot-utility-report --merchant-id merchant_demo_refurb_tech
python -m behavior_lab counterpilot-rule-sim --merchant-id merchant_demo_refurb_tech --rule '{"rule_type":"counter_percent_above_offer","counter_markup_pct":0.08}'
python -m behavior_lab counterpilot-shadow-recommend --merchant-id merchant_demo_refurb_tech --offer-id offer_current_001 --config '{"minimum_comparable_mature_outcomes":5,"floor_buffer":10.0}'
python -m behavior_lab counterpilot-experiment preregister --experiment-id exp_shadow_adoption_001 --experiment-type shadow_recommendation_exposure --merchant-id merchant_demo_refurb_tech --planned-units 30
python -m behavior_lab counterpilot-experiment assign --experiment-id exp_shadow_adoption_001 --merchant-id merchant_demo_refurb_tech --offer-id offer_current_001
python -m behavior_lab counterpilot-experiment outcome --assignment-id cp_exp_assign_EXAMPLE --outcomes '{"merchant_adopted_recommendation":true}'
python -m behavior_lab counterpilot-experiment report --experiment-id exp_shadow_adoption_001
python -m behavior_lab counterpilot-run-local-fixture --data-dir C:\OfferLabData\counterpilot_core
python -m behavior_lab counterpilot-transaction-inspect --data-dir C:\OfferLabData\counterpilot_core --merchant-namespace merchant_demo_refurb:store_demo_shopify --transaction-id txn_counterpilot_loop_001
python -m behavior_lab counterpilot-research-export --data-dir C:\OfferLabData\counterpilot_core --merchant-id merchant_demo_refurb --store-id store_demo_shopify
```

## Gates

The profit-optimization gate currently requires:

- merchant-specific learning consent
- at least 80% cost-basis coverage
- at least 30 mature paid outcomes
- no customer PII in model/event features

Until those pass, the correct stage is `transaction_surface`, not automation.
