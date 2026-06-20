# Behavior Discovery Lab

Behavior Discovery Lab is a local research harness for discovering and falsifying executable behavioral hypotheses.

A hypothesis may be a compact formula, rule, threshold, state model, nearest-neighbor policy, or another reloadable predictor. The laboratory keeps creativity and judgment separate:

```text
Generate hypotheses
        ↓
Fit only on campaign training data
        ↓
Iterate on development data
        ↓
Freeze the selected model artifact and data cut
        ↓
Spend one hidden lockbox query
        ↓
Collect genuinely new observations
        ↓
Spend one prospective query
```

This repository is an infrastructure MVP, not a validated human-behavior oracle.

## What is implemented

- Hidden synthetic behavior worlds with deterministic, restart-safe event generation.
- Append-only JSONL ledger with a hash chain and local-process write locking.
- Immutable, chronological split manifests scoped to research campaigns.
- Pre-decision snapshots that structurally exclude known post-decision fields.
- A bounded mathematical DSL for formulas.
- A heterogeneous model foundry: base rate, recent rate, nearest neighbor, threshold, decision stump, two-state model, linear formula, and symbolic search.
- Campaign-scoped, hashed, reloadable model artifacts.
- Development diagnostics, residuals, counterexamples, paired comparison, and complexity frontiers.
- Persistent hidden/prospective evaluation budgets.
- Cross-campaign protection against reusing previously queried hidden cases.
- Preregistered randomized experiments with assignment and outcome integrity checks.
- Deterministic, restart-safe randomization streams that remain independent across repeated registrations of the same design.
- Difference-in-means and inverse-probability-weighted treatment comparisons.
- A provider-neutral, validated LLM hypothesis-generator seam.
- Locked, idempotent synthetic batch stress runs.

The core runtime uses only Python's standard library.

## Quick start

Python 3.11 or newer is required.

```bash
python -m pip install -e .
python -m pytest
python -m behavior_lab stress-test \
  --data-dir runs/stress-habit \
  --world habit \
  --episodes 160 \
  --seed 17
```

Run the complete demonstration:

```bash
python -m behavior_lab demo \
  --data-dir runs/demo \
  --world habit \
  --episodes 180 \
  --iterations 3 \
  --offline-trials 12 \
  --prospective-episodes 40
```

The demo resets its output directory by default. Pass `--no-reset` only when you intentionally want to continue the same ledger.

## What you should see

The stress report should show:

```json
{
  "temporal_firewall_ok": true,
  "split_chronology_ok": true,
  "initial_prospective_empty": true,
  "hidden_payload_redacted": true
}
```

The initial campaign should contain training, development, and hidden cases, but **zero prospective cases**. Prospective means observations first recorded after a model freeze; it does not mean "the newest fraction of an existing file."

The discovery loop should:

1. Create a fresh campaign for each offline iteration.
2. Use only training and development results while mutating hypotheses.
3. Leave every intermediate hidden split unqueried.
4. Select one final candidate using development data.
5. Freeze the exact persisted artifact, split snapshot, and data cutoff.
6. Submit that frozen candidate once to the final hidden lockbox.
7. Generate new observations after the freeze.
8. Submit the same frozen artifact once to the prospective evaluator.

## Campaign semantics

A campaign is an immutable view of the observations available when it starts.

```text
Existing observations at campaign creation
  → chronological training/development/hidden assignment

New observations before freeze
  → staging

New observations after freeze
  → prospective, bound to that freeze ID
```

Staging data never moves backward into a campaign's training set. Start a new campaign to incorporate it.

Model artifacts are campaign-scoped. Reopen `ResearchAPI` using the same campaign ID to reload its models:

```python
api = ResearchAPI(gym, campaign_id="experiment-001")
# ... fit model ...

reloaded = ResearchAPI(gym, campaign_id="experiment-001")
```

A different campaign may deliberately refit or import a model, but it does not silently inherit another campaign's fitted registry.

## Lockbox limits

`ResearchAPI` defaults to:

- One hidden aggregate submission per hidden case set.
- One prospective aggregate submission per frozen candidate.

Renaming a campaign does not reset a hidden budget when the hidden cases overlap a previously queried set.

Hidden and prospective responses omit raw labels, failure rows, direct prevalence, and baseline lift. However, **any aggregate scoring metric carries some statistical information**. The one-query budget is therefore a scientific discipline, not perfect information-theoretic secrecy.

`ResearchAPI` is a logical boundary inside one Python process. Do not give untrusted generated code direct filesystem access to the ledger or evaluator. A production LLM researcher should run out of process and receive only typed RPC tools.

## CLI

```bash
python -m behavior_lab seed-world --data-dir runs/world --world habit --episodes 200 --seed 7
python -m behavior_lab run-loop --data-dir runs/world --world habit --iterations 4
python -m behavior_lab verify-ledger --data-dir runs/world
python -m behavior_lab stress-test --data-dir runs/matrix --episodes 120 --matrix
python -m behavior_lab batch-stress \
  --data-dir runs/batch \
  --worlds habit,two_mode,threshold,nonstationary,confounded \
  --seeds 11,23,47 \
  --episode-counts 100,300
python examples/first_research_session.py
```

## Automated background research

Start by automating synthetic falsification, not real-life interventions.

A safe researcher may repeatedly use:

```text
inspect_schema
list_variables
describe_target
query_training_data
submit_hypothesis
fit_hypothesis
evaluate on development
inspect residuals and counterexamples
propose synthetic experiment
run preregistered offline experiment
```

A gatekeeper should exclusively control:

```text
hidden evaluation
candidate freeze
prospective evaluation
real intervention launch
```

See [`docs/AUTOMATION.md`](docs/AUTOMATION.md) for the recommended worker lifecycle and budgets.

## Scientific interpretation

Do not celebrate a model because its prose sounds human or because it wins once on development data.

A credible progression is:

```text
beats base rate on development
→ survives multiple seeds
→ survives a hidden chronological block
→ is frozen
→ survives genuinely future observations
→ predicts intervention direction
→ remains competitive at lower complexity
```

The stress tester's mechanism score is only exact-variable recall against a synthetic hidden world. It is not proof that the recovered equation is causally or mathematically equivalent.

## Current limitations

- The LLM adapter validates proposals but does not include a hosted or local model client.
- The evaluator is a logical in-process boundary, not a hostile-code sandbox.
- The formula DSL is intentionally small.
- The causal layer supports randomized binary comparisons, not arbitrary observational causal identification.
- Personal data adapters are intentionally absent.
- Pre-decision structural filtering cannot detect semantic leakage hidden inside misleading field names or prose.
- Aggregate lockbox metrics leak limited information by their nature.
- Real credibility requires enough future observations collected after a model freeze.

See [`docs/CODE_REVIEW.md`](docs/CODE_REVIEW.md) for the stress-test findings and fixes applied to this version.
