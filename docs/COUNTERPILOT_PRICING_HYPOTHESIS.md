# Counterpilot Pricing Hypothesis

This is a private-beta pricing hypothesis, not a billing implementation.

## Pricing Principles

- Charge only for clearly attributable Counterpilot-mediated usage or a simple
  flat pilot fee.
- Do not charge on "recovered revenue."
- Do not charge on "profit lift."
- Do not claim counterfactual lift.
- Do not imply Counterpilot caused revenue that was only observed.

## Candidate Private-Beta Models

### Flat Pilot

```text
$99-$299/month
```

Use when the merchant wants predictable cost during a short pilot and does not
want transaction-aligned billing yet.

### Transaction-Aligned

```text
About 1% of Counterpilot-mediated paid negotiated orders
```

Use only when the merchant accepts usage-aligned pricing. This should be based
on paid negotiated order value that flowed through Counterpilot, not inferred
lift.

### Hybrid

```text
$49/month + about 1% of Counterpilot-mediated paid negotiated orders
```

Use when a low platform fee plus usage alignment feels easier to approve.

## What Not To Price Against

Do not price against:

- Recovered revenue.
- Avoided discounting.
- Hypothetical profit lift.
- AI-generated recommendations.
- Cross-merchant insights.
- Conversion lift.

## First Conversation Question

Ask:

```text
Would you rather evaluate this as a flat private pilot, a usage-aligned fee on
Counterpilot-mediated paid negotiated orders, or a low monthly fee plus usage?
```
