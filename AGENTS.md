# Repository Instructions

Campaign 001 is a real-use observational collection layer, not a modeling task.

- Do not change the Campaign 001 target, feature names, protected outcome names, bridge schema, lockbox behavior, split semantics, or intervention rule without an explicit versioned campaign change.
- Do not add network calls, LLM calls, prediction output, model fitting, or aggregate behavioral findings to `campaign-001-capture`.
- Keep raw local captures append-style: preserve sealed pre-decision snapshots and record corrections as amendment events instead of silently overwriting values.
- Keep Behavior Lab and Behavior Discovery Lab separated by immutable bridge exports. Do not make them share a mutable database.
- The first 50 Campaign 001 episodes are natural observations. Do not add randomized interventions to the collector during this block.

Campaign 002 / OfferLab is the commercial direction.

- Keep Stage 1 read-only. Do not add code that accepts, declines, counters, discounts, or otherwise mutates eBay state.
- Use normalized immutable snapshots and seller-approved decisions; automated marketplace action requires separate evidence and explicit approval.
- Optimize contribution margin and listing-day economics, not acceptance rate or gross sales volume.
- Keep eBay API code behind adapter boundaries so the experiment ledger remains marketplace-independent.
