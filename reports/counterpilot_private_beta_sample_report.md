# Counterpilot Merchant Report

Generated at: 2026-06-23T20:00:00.000Z
Production evidence: false
production_evidence: false

This report summarizes Counterpilot-mediated negotiated orders and their
observed lifecycle outcomes. It does not estimate conversion lift, profit lift,
recovered revenue, or what would have happened without Counterpilot.

Counterpilot is not a recommendation model. Merchant accept, counter, and
decline decisions were manual.

## Summary

- Offers submitted: 3
- Paid negotiated orders: 2
- Current mature transactions: 2
- Current mature margin: $230.00 USD
- Current open return exposure blocking maturity: 1

## Offer Funnel

| Step               | Count |
| ------------------ | ----- |
| offers_submitted   | 3     |
| merchant_accepted  | 1     |
| merchant_countered | 2     |
| merchant_declined  | 0     |
| buyer_accepted     | 2     |
| checkout_created   | 2     |
| order_created      | 2     |
| paid               | 2     |
| current_mature     | 2     |

## Mature Margin Summary

| Metric        | Value         |
| ------------- | ------------- |
| paid_total    | $1,220.00 USD |
| refund_total  | $15.00 USD    |
| net_revenue   | $1,205.00 USD |
| product_cost  | $840.00 USD   |
| shipping_cost | $70.00 USD    |
| platform_fee  | $65.00 USD    |
| return_loss   | $0.00 USD     |
| mature_margin | $230.00 USD   |

## Refund And Return Impact

| Metric                          | Value      |
| ------------------------------- | ---------- |
| refunded_transactions           | 1          |
| refund_total_across_paid        | $15.00 USD |
| latest_refund_total_across_paid | $15.00 USD |
| current_open_return_exposure    | 1          |
| current_closed_return_exposure  | 1          |

## Margin Leakage

| Leakage component | Amount      |
| ----------------- | ----------- |
| refunds           | $15.00 USD  |
| product_cost      | $840.00 USD |
| shipping_cost     | $70.00 USD  |
| platform_fee      | $65.00 USD  |
| return_loss       | $0.00 USD   |

## Product/SKU Breakdown

- Sample Snowboard Bundle (product_a12f)
  - Offers: 2
  - Paid: 2
  - Current mature: 2
  - Refunds: $15.00 USD
  - Mature margin: $230.00 USD
- Sample Jacket (product_b87c)
  - Offers: 1
  - Paid: 0
  - Current mature: 0
  - Refunds: $0.00 USD
  - Mature margin: $0.00 USD

## Offer-To-Asking Breakdown

Offer-to-asking ratios are unavailable because the current storefront event
schema does not persist asking_price_minor.

## Safe Transaction Ledger

- sample_txn_001
  - Product: Sample Snowboard Bundle (product_a12f)
  - Lifecycle: mature
  - Maturity status: current_mature
  - Offer: $610.00 USD
  - Selected amount: $610.00 USD
  - Paid total: $610.00 USD
  - Refund total: $0.00 USD
  - Return exposure: closed
  - Mature margin: $155.00 USD
  - Production evidence: false
- sample_txn_002
  - Product: Sample Snowboard Bundle (product_a12f)
  - Lifecycle: mature
  - Maturity status: current_mature
  - Offer: $595.00 USD
  - Selected amount: $610.00 USD
  - Paid total: $610.00 USD
  - Refund total: $15.00 USD
  - Return exposure: none
  - Mature margin: $75.00 USD
  - Production evidence: false
- sample_txn_003
  - Product: Sample Jacket (product_b87c)
  - Lifecycle: paid
  - Maturity status: maturity_blocked_open_return
  - Offer: $180.00 USD
  - Selected amount: $195.00 USD
  - Paid total: $195.00 USD
  - Refund total: $0.00 USD
  - Return exposure: open
  - Mature margin: not current
  - Production evidence: false

## Assumptions Used

| Assumption            | Value                         |
| --------------------- | ----------------------------- |
| schema_version        | counterpilot.margin_config.v1 |
| maturity_window_days  | 0                             |
| default_product_cost  | $420.00 USD                   |
| default_shipping_cost | $35.00 USD                    |
| default_platform_fee  | $32.50 USD                    |
| default_return_loss   | $0.00 USD                     |
| currency              | USD                           |

## Data-Quality / Reconciliation Notes

- Stale mature events excluded from current margin view: 1
- Operational refund refs counted: 1
- Operational return refs counted: 2
- Refund ref statuses: processed: 1
- Return ref statuses: processed: 2
- Reconciliation holds: 0

## Language Boundaries

- Non-causal report: true
- Recommendation model: false
- Merchant decisions: manual
