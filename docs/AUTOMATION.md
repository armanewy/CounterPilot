# Automating the Research Loop

## Recommended process boundary

Run the hypothesis-generating LLM in a separate process or container. It should not mount the ledger, model artifacts, hidden labels, or prospective records.

Expose a small RPC surface modeled on `ResearchAPI`:

```text
Allowed repeatedly
- inspect_schema
- list_variables
- describe_target
- query_training_data
- submit_hypothesis
- fit_hypothesis
- evaluate_development
- compare_development_models
- inspect_residuals
- inspect_counterexamples
- propose_synthetic_experiment
- run_offline_experiment

Gatekeeper only
- evaluate_hidden
- freeze_candidate
- evaluate_prospective
- launch_real_intervention
```

## One research campaign

1. Create a new campaign from all observations currently available.
2. Give the researcher the training schema and permitted training rows.
3. Allow a bounded number of hypothesis proposals and development evaluations.
4. Require every hypothesis to contain assumptions and falsification conditions.
5. Select one candidate using development data.
6. Freeze the exact artifact and current data cut before opening any lockbox.
7. Spend one hidden query and use it only as a preregistered go/no-go check.
8. If continuing, collect genuinely new observations without refitting.
9. Spend one prospective query on the same frozen artifact.
10. Close the campaign. Never continue tuning against its hidden or prospective result.

## Suggested worker budget

```yaml
campaign_id: habit-seed-101-campaign-01
budgets:
  hypothesis_proposals: 20
  fitted_candidates: 20
  development_evaluations: 30
  residual_queries: 10
  synthetic_experiments: 5
  synthetic_trials_per_experiment: 24
  hidden_submissions: 1
  prospective_submissions: 1
limits:
  max_formula_terms: 8
  max_cycles: 5
  max_runtime_minutes: 30
```

Budgets should be enforced outside the LLM process as well as in prompts.

## LLM system instruction

```text
You are a behavioral hypothesis researcher.

Propose small executable hypotheses, not persuasive psychological stories.
Use only variables exposed by the tool interface.
Every hypothesis needs assumptions and an explicit falsification condition.
Compare every candidate with base-rate and recent-rate baselines.
Use development data for iteration.
Never request or infer hidden/prospective labels.
Do not claim causality from observational association.
Prefer experiments where plausible models disagree most.
Retire hypotheses that repeatedly fail.
Stop when the tool-enforced budget is exhausted.
```

## Batch synthetic research first

Before importing personal data, run independent synthetic jobs across:

- Every available world.
- At least 20 unseen seeds.
- Several sample sizes.
- Missing and irrelevant variables.
- Noise and nonstationarity.

Use a fresh run directory per world/seed/sample-size combination. The `batch-stress` command is locked and idempotent for this purpose.

## Promotion criterion

A useful first milestone is:

> Across at least 20 unseen seeds, the automated researcher selects one model before hidden evaluation, beats the base-rate baseline on hidden and prospective blocks, and predicts the direction of at least one previously unseen intervention more often than chance.

Failure on the confounded world is especially informative. A model that predicts well using a correlated proxy but assigns the wrong intervention effect has not recovered the mechanism.
