# Counterpilot Data Boundaries

Wave 1B separates Counterpilot commerce operations from research use.

## Stores

### OperationalTransactionStore

Purpose: fulfill commerce workflows and deletion obligations.

Allowed operational fields:

- Merchant and store identifiers.
- Shopify resource IDs for order, checkout, customer, fulfillment, and payment records.
- Contact-delivery reference.
- Checkout URL reference.
- Fulfillment and payment state.
- Minimal customer data required for commerce, stored only behind the encrypted-at-rest adapter.
- Retention and deletion metadata.

This store is not a model feature source. Model feature builders reject an
`OperationalTransactionStore` directly.

### ResearchEventStore

Purpose: append-only research ledger and model-ready datasets.

Allowed research fields:

- Merchant and store identifiers.
- Pseudonymous session and buyer identifiers.
- Offer context.
- Decisions.
- Outcomes.
- Financial components.
- Source lineage describing the transformation, not direct operational IDs.

Direct customer identifiers, Shopify IDs, checkout URLs, contact details, IP
addresses, addresses, raw buyer/customer IDs, metadata leaks, and exception
messages containing PII are rejected before append or dataset export.

## Consent

`ConsentRecord` contains:

- Merchant ID.
- Store ID.
- Consent policy version.
- Policy hash.
- Granted purposes.
- Prohibited purposes.
- `granted_at`.
- `revoked_at`.
- Provenance.

Rules:

- Merchant-specific model use requires active consent for the exact purpose.
- Cross-merchant training defaults to prohibited.
- Cross-merchant training requires active consent for the cross-merchant purpose
  from every merchant/store pair in the dataset.
- Revocation appends a new consent record and blocks new training immediately.
- Revocation does not rewrite existing research ledger events.

## Identity Mapping

Operational identifiers must pass through the ephemeral mapping layer before
research export. The mapping layer uses a secret, merchant ID, store ID,
namespace, raw identifier, and rotation ID to derive a pseudonymous ID.

Hashing an email address alone is not acceptable anonymization. The research
ledger must never store the raw email address or a bare hash of it.

The mapping layer supports:

- Rotation for new pseudonyms.
- Deletion by pseudonymous ID.
- Deletion by raw subject.
- Deletion by rotation.

Research events remain usable after operational PII and mapping entries are
deleted because they retain pseudonymous IDs, offer context, decisions,
outcomes, financial components, consent lineage, and dataset lineage.

## Production Artifacts

Production artifacts must retain:

- Dataset ID.
- Event hashes.
- Merchant/store pairs.
- Research record type.
- Consent record IDs and hashes.
- Consent policy version and policy hash.
- Purpose used to build the dataset.

Artifact creation rejects payloads that contain PII.

## Network Boundary

Wave 1B storage primitives make no network calls. Production integrations must
remain behind adapters and must not bypass the operational/research separation.
