# Counterpilot Privacy Boundaries

Counterpilot has three data zones:

```text
operational storage
-> merchant reports
-> research exports / proof artifacts
```

Data may only move from left to right after redaction and validation.

## Operational Storage

Operational storage may hold data needed to run commerce:

- Shopify access tokens.
- Shopify resource IDs.
- Checkout or invoice URLs.
- Buyer contact references.
- Customer contact data required for delivery.
- Draft order and order bindings.

Operational data must stay server-side and must not be copied into reports,
research exports, or proof artifacts.

## Merchant Reports

Reports are merchant-facing business summaries. They may contain economic and
lifecycle facts:

- number of offers
- offer funnel state counts
- negotiated revenue
- cost basis
- shipping and fulfillment costs
- fees
- refunds
- return loss
- maturity status
- mature contribution margin
- product/SKU labels if they are not direct customer identifiers

Reports must not contain:

- access tokens
- refresh tokens
- checkout URLs
- raw Shopify GIDs
- customer names
- customer emails
- customer addresses
- phone numbers
- IP addresses
- raw buyer messages
- free-form buyer negotiation text

## Research Exports

Research exports may contain only pseudonymous and economic fields:

- pseudonymous buyer/session identifiers
- feature values available before decisions
- merchant decisions
- outcomes
- financial components
- consent lineage
- dataset lineage

Research exports must never contain operational identifiers or customer PII.

## Proof Artifacts

Dev-store and release proof artifacts should prove the lifecycle without
leaking operational values.

Allowed:

- app version
- git commit
- store mode
- timestamp
- transaction ID
- redacted event references
- transition sequence
- hashed Shopify resource references
- final mature-margin components
- report hash
- research export hash
- PII scan result
- manual steps completed
- skipped steps and reasons

Forbidden:

- access tokens
- refresh tokens
- checkout URLs
- raw Shopify GIDs
- raw order IDs
- customer names
- customer emails
- addresses
- phone numbers
- raw buyer message text

## Buyer Messages

Private Beta v0 should not collect raw buyer messages.

The buyer form should start with:

```text
Offer amount
Email
Optional quantity
```

Structured reason fields can be considered later. Free-text negotiation creates
moderation, support, privacy, and redaction risk before the product needs it.

## Dev/Test Evidence

Shopify development-store and test-payment runs must be marked:

```json
{
  "production_evidence": false
}
```

Do not let test orders support claims about production margin, conversion, or
causal lift.

## Blocking Failure

Any report, proof artifact, or research export containing raw Shopify IDs,
checkout URLs, tokens, customer contact fields, or raw buyer messages is a
release blocker.
