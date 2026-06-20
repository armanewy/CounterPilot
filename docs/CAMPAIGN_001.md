# Campaign 001 - Task Initiation

This is the first real-use campaign. It is intentionally observational.

## Freeze

`v0.3.0` is the frozen laboratory tag for this campaign.

## Target

```text
Did I begin the intended task within 10 minutes?
```

## Pre-Decision Fields

Schema `1.1` is active. Schema `1.0` is retained in `campaigns/campaign_001_task_initiation/campaign.schema.v1.0.json`.

- `task_type`
- `time_of_day`
- `fatigue`: integer `0..3`
- `ambiguity`: integer `0..3`
- `estimated_minutes`
- `first_step_explicit`
- `has_deadline`
- `deadline_hours`: non-negative number when `has_deadline` is true; `null` when `has_deadline` is false
- `recent_context_switches`
- `public_commitment`

## Protected Outcomes

- `started_within_10_minutes`
- `start_latency_seconds`
- `worked_for_20_minutes`
- `completed_that_day`

## Rule

Run five pilot episodes first and retain them in the ledger as `collection_phase: pilot`. Then collect 50 natural episodes before any intervention. The next success criterion is not another test suite; it is a frozen, simple hypothesis surviving genuinely future observations.

Eligibility rule:

```text
Record any self-directed task expected to require at least ten minutes when I genuinely intend to begin it within the next fifteen minutes.
```

Exclude emergencies, meetings already in progress, trivial actions, and tasks someone else is actively directing.
