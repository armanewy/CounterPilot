# Campaign 001 Collection Protocol

Campaign 001 asks:

```text
Did I begin the intended task within 10 minutes?
```

## Scope

Use `python -m behavior_lab campaign-001-capture` to collect local, manual episode records. The collector is only an integrity layer. It does not fit models, call an LLM, make predictions, summarize behavior, or run interventions.

## Pre-Decision Capture

Run `start` before deciding what to do:

```powershell
python -m behavior_lab campaign-001-capture start
```

The collector records these pre-decision fields:

- `task_type`
- `time_of_day`
- `fatigue`
- `ambiguity`
- `estimated_minutes`
- `first_step_explicit`
- `deadline_hours`
- `recent_context_switches`
- `public_commitment`

It also records local provenance: episode UUID, timestamps, timezone, collector version, monotonic timer, available actions, source statuses, eligibility rule version, and whether the episode is part of the natural observational block.

The pre-decision snapshot is hashed and sealed before outcomes are accepted. Outcome fields are rejected during `start`.

## Missingness

Missing values are explicit. The collector will not fill blanks. A capture with unavailable required fields is kept as `incomplete` and cannot be bridge-imported as a completed episode until a valid pre-decision snapshot exists.

The frozen bridge schema still requires numeric `deadline_hours` for completed imports. If the deadline is unavailable, leave it unavailable and treat the episode as incomplete rather than inventing a value.

## Follow-Ups

The collector creates follow-up horizons:

- T+10 minutes: `started_within_10_minutes`, `start_latency_seconds`
- T+20 minutes: `worked_for_20_minutes`
- End of day: `completed_that_day`

Outcome sources must use one of:

- `manual_observation`
- `timer_assisted`
- `system_assisted`
- `unavailable`

There is no system monitoring in this version. The source enum and adapter interface are present so a future local source can be added without changing the protected outcome contract.

## Finalization

Finalize after outcomes are known:

```powershell
python -m behavior_lab campaign-001-capture finalize --episode-id c001_...
```

Finalization creates a bridge JSONL export, adds a canonical `source_hash`, validates the export, imports it into `data/campaign_001_task_initiation/ledger.jsonl`, verifies the ledger hash chain, and prints the episode ID plus ledger record ID.

Repeating finalization for the same episode is idempotent when the source hash matches.

## Corrections And Invalidations

Corrections are amendment records:

```powershell
python -m behavior_lab campaign-001-capture amend --episode-id c001_... --field fatigue --value 2 --reason "entered wrong value"
```

Amendments do not change the sealed pre-decision hash. If a capture is unusable, invalidate the unfinished local artifact:

```powershell
python -m behavior_lab campaign-001-capture invalidate --episode-id c001_... --reason "decision boundary was ambiguous"
```

Completed bridge-imported episodes cannot be invalidated by this collector.

## Missed Episodes

If an eligible task occurred but was not captured before the decision, record it without creating a completed episode:

```powershell
python -m behavior_lab campaign-001-capture missed
```

This keeps sampling bias visible in `status`.
