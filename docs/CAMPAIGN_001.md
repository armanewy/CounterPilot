# Campaign 001 - Task Initiation

This is the first real-use campaign. It is intentionally observational.

## Freeze

`v0.3.0` is the frozen laboratory tag for this campaign.

## Target

```text
Did I begin the intended task within 10 minutes?
```

## Pre-Decision Fields

- `task_type`
- `time_of_day`
- `fatigue`: integer `0..3`
- `ambiguity`: integer `0..3`
- `estimated_minutes`
- `first_step_explicit`
- `deadline_hours`
- `recent_context_switches`
- `public_commitment`

## Protected Outcomes

- `started_within_10_minutes`
- `start_latency_seconds`
- `worked_for_20_minutes`
- `completed_that_day`

## Rule

Collect 50 natural episodes before any intervention. The next success criterion is not another test suite; it is a frozen, simple hypothesis surviving genuinely future observations.
