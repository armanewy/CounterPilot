# Campaign 001 Daily Use

## Before The Task Decision

Eligible tasks are self-directed tasks expected to require at least ten minutes when you genuinely intend to begin within the next fifteen minutes. Exclude emergencies, meetings already in progress, trivial actions, and tasks someone else is actively directing.

Run:

```powershell
python -m behavior_lab campaign-001-capture start
```

Answer only with information available before deciding whether to start. Put private notes in the optional note field, not in a pre-decision feature. For no deadline, answer `false` for `has_deadline` and leave `deadline_hours` blank.

For the five-episode pilot, use:

```powershell
python -m behavior_lab campaign-001-capture start --pilot
```

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

For a missed pilot episode, use `--pilot`.

## Operational Status

Run:

```powershell
python -m behavior_lab campaign-001-capture status
```

Status reports operational counts, pending follow-ups, missed eligible episodes, and ledger validity only. It intentionally does not report behavioral findings.

Run status at least once daily during collection.

## Optional Local Helpers

A Windows shortcut or toast reminder may point to `python -m behavior_lab campaign-001-capture start`, but no reminder service is required. If reminders become annoying or change behavior, remove them and keep manual entry.
