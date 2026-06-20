# Repository Instructions

Campaign 001 is a real-use observational collection layer, not a modeling task.

- Do not change the Campaign 001 target, feature names, protected outcome names, bridge schema, lockbox behavior, split semantics, or intervention rule without an explicit versioned campaign change.
- Do not add network calls, LLM calls, prediction output, model fitting, or aggregate behavioral findings to `campaign-001-capture`.
- Keep raw local captures append-style: preserve sealed pre-decision snapshots and record corrections as amendment events instead of silently overwriting values.
- Keep Behavior Lab and Behavior Discovery Lab separated by immutable bridge exports. Do not make them share a mutable database.
- The first 50 Campaign 001 episodes are natural observations. Do not add randomized interventions to the collector during this block.
