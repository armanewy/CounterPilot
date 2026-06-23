# Counterpilot Reports

Counterpilot reports are merchant-facing accounting views over the append-only
transaction ledger. They do not train models, choose prices, automate counters,
or claim profit lift.

## Merchant Utility Report

The `counterpilot-report` command summarizes observed commerce outcomes:

- offer funnel counts from submitted offers through mature outcomes
- mature contribution-margin components
- margin leakage from free shipping, refunds, unpaid accepted offers, expired
  checkout links, missing cost basis, missing fees, and immature outcomes
- product, inventory-age, and offer-to-asking-ratio breakdowns
- a retrospective deterministic rule simulator labeled as non-causal

Mature-margin totals are marked provisional when required components are
missing. Incomplete, cancelled, refunded, and immature transactions remain in
the report instead of disappearing from the denominator.

## Commands

```powershell
python -m behavior_lab counterpilot-report --data-dir C:\OfferLabData\counterpilot\transaction_core --format json
python -m behavior_lab counterpilot-report --data-dir C:\OfferLabData\counterpilot\transaction_core --format markdown --output report.md
python -m behavior_lab counterpilot-report --merchant-namespace merchant_demo_refurb:store_demo_shopify
```

The report refuses PII in its output. Shopify resource IDs, checkout URLs,
customer names, addresses, emails, phone numbers, and free-form buyer messages
must remain in operational storage, not merchant research/report outputs.
