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

| Product                                | Offers | Paid | Current mature | Refunds    | Mature margin |
| -------------------------------------- | ------ | ---- | -------------- | ---------- | ------------- |
| Sample Snowboard Bundle (product_a12f) | 2      | 2    | 2              | $15.00 USD | $230.00 USD   |
| Sample Jacket (product_b87c)           | 1      | 0    | 0              | $0.00 USD  | $0.00 USD     |

## Offer-To-Asking Breakdown

Offer-to-asking ratios are unavailable because the current storefront event
schema does not persist asking_price_minor.

## Safe Transaction Ledger

| Transaction    | Product                                | Lifecycle | Maturity status              | Offer       | Selected amount | Paid total  | Refund total | Return exposure | Mature margin | Production evidence |
| -------------- | -------------------------------------- | --------- | ---------------------------- | ----------- | --------------- | ----------- | ------------ | --------------- | ------------- | ------------------- |
| sample_txn_001 | Sample Snowboard Bundle (product_a12f) | mature    | current_mature               | $610.00 USD | $610.00 USD     | $610.00 USD | $0.00 USD    | closed          | $155.00 USD   | false               |
| sample_txn_002 | Sample Snowboard Bundle (product_a12f) | mature    | current_mature               | $595.00 USD | $610.00 USD     | $610.00 USD | $15.00 USD   | none            | $75.00 USD    | false               |
| sample_txn_003 | Sample Jacket (product_b87c)           | paid      | maturity_blocked_open_return | $180.00 USD | $195.00 USD     | $195.00 USD | $0.00 USD    | open            | not current   | false               |

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
