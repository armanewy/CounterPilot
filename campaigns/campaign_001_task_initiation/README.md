# Campaign 001 - Task Initiation

Target:

```text
Did I begin the intended task within 10 minutes?
```

Active campaign schema: `1.1`. The old `1.0` schema is preserved in `campaign.schema.v1.0.json`.

Start with five pilot episodes, then 50 natural episodes and no interventions. Use manual entry if needed; do not fabricate backfilled labels or infer missing fields.

Eligibility rule:

```text
Record any self-directed task expected to require at least ten minutes when I genuinely intend to begin it within the next fifteen minutes.
```

Exclude emergencies, meetings already in progress, trivial actions, and tasks someone else is actively directing.

## Manual Entry Flow

1. Write one raw JSON object per episode using the fields in `campaign.json`.
   Use `has_deadline: false` with `deadline_hours: null` when there is no deadline.
2. Keep outcomes only under `protected_outcome`.
3. Add source hashes:

```powershell
python -m behavior_lab bridge-hash --input manual_raw.jsonl --output export_hashed.jsonl
```

4. Validate:

```powershell
python -m behavior_lab bridge-validate --input export_hashed.jsonl
```

5. Import into an isolated ledger:

```powershell
python -m behavior_lab bridge-import --input export_hashed.jsonl --data-dir data/campaign_001_task_initiation
```

Use `data/campaign_001_task_initiation/ledger.jsonl` as the immutable bridge output. Do not share a mutable database between Behavior Lab and Behavior Discovery Lab.

## After 50 Natural Episodes

Create a research campaign from the imported ledger and compare:

- Base rate
- Recent rate
- Nearest episode
- Sparse logistic formula
- Threshold rule
- Two-state model
- LLM-proposed formulas

Do not inspect hypotheses, model performance, or randomized A/B interventions until the natural observational block has been collected.
