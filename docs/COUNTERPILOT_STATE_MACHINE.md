# Counterpilot State Machine

Counterpilot transaction state is deterministic and append-only. The state
machine records one negotiated commerce transaction from offer submission to a
mature economic outcome. It does not call Shopify, train models, recommend
prices, or execute seller actions.

## Event Contract

Each transition event uses schema `counterpilot.transaction_event.v1` and must
include:

- `event_id`
- `merchant_namespace`
- `transaction_id`
- `occurred_at`
- `received_at`
- `source`
- `schema_version`
- `idempotency_key`
- `transition_to`

The idempotency key is scoped by merchant namespace and transaction ID. Replayed
identical events are no-ops; reusing a key with different content is rejected.

## States

The explicit states are:

```text
offer_submitted
offer_expired
merchant_accepted
merchant_declined
merchant_countered
buyer_countered
buyer_accepted
buyer_declined
checkout_created
checkout_expired
order_created
payment_pending
paid
cancelled
partially_refunded
fully_refunded
return_opened
return_received
return_closed
mature
```

Invalid transitions are rejected. Shopify-style webhook events that arrive
before their local predecessor may be stored as pending and reconciled when the
predecessor appears. Prior events are never overwritten.

## Economic Rules

Money is represented as integer minor units plus an ISO currency code:

```json
{"amount_minor": 76000, "currency": "USD"}
```

Mixed-currency arithmetic is rejected unless the event carries an explicit
conversion record. Quantity, item discounts, order discounts, and shipping
discounts are represented explicitly. A shipping discount does not erase
merchant shipping cost; free shipping still requires positive shipping cost.

## Action Boundaries

Merchant and system actions must record:

- available actions
- recommendation
- merchant decision
- executed action

These are separate fields so a later audit can distinguish what the system
said, what the merchant chose, and what was actually executed.

## Mature Outcome

A `mature` transition requires:

- payment resolution
- refund/return maturity date
- reconciled fees
- reconciled fulfillment costs
- mature contribution margin

No transition event may contain names, addresses, email addresses, phone
numbers, or free-form buyer messages.
