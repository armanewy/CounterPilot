# Campaign 001 Daily Use

## Before The Task Decision

Run:

```powershell
python -m behavior_lab campaign-001-capture start
```

Answer only with information available before deciding whether to start. Put private notes in the optional note field, not in a pre-decision feature.

## Afterward

When outcomes are known, finalize:

```powershell
python -m behavior_lab campaign-001-capture finalize --episode-id c001_...
```

If the terminal was closed after `start`, list resumable episodes:

```powershell
python -m behavior_lab campaign-001-capture resume
```

Then finalize the episode by ID.

## If You Forgot To Start Capture

Run:

```powershell
python -m behavior_lab campaign-001-capture missed
```

Do not backfill a completed episode after the task decision.

## Operational Status

Run:

```powershell
python -m behavior_lab campaign-001-capture status
```

Status reports operational counts, pending follow-ups, missed eligible episodes, and ledger validity only. It intentionally does not report behavioral findings.

## Optional Local Helpers

A Windows shortcut or toast reminder may point to `python -m behavior_lab campaign-001-capture start`, but no reminder service is required. If reminders become annoying or change behavior, remove them and keep manual entry.
