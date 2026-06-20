# Stress Testing Behavior Discovery Lab

The stress suite is intended to falsify the laboratory itself before the laboratory is trusted to evaluate behavioral theories.

## One-world audit

```bash
python -m behavior_lab stress-test \
  --data-dir runs/stress-habit \
  --world habit \
  --episodes 160 \
  --seed 17
```

Checks include:

- pre-decision snapshots exclude known post-decision fields and provenance;
- campaign training, development, and hidden blocks are chronological;
- the initial prospective block is empty;
- hidden output omits raw labels, failure rows, direct prevalence, and baseline lift;
- the best development candidate is compared with the base-rate baseline;
- the best genuinely discovered formula receives evaluator-only hidden-driver variable recall;
- a separate known-driver probe checks whether the formula language can express the hidden variables;
- intervention-direction predictions are compared with the synthetic world's true intervention direction.

`best_discovered_formula_hidden_driver_recall` is variable recall only. It does not prove equation, threshold, sign, coefficient, dynamics, or causal equivalence.

## World matrix

```bash
python -m behavior_lab stress-test \
  --data-dir runs/stress-matrix \
  --episodes 120 \
  --seed 17 \
  --matrix
```

The matrix covers:

- habit plus override;
- latent two-mode behavior;
- threshold behavior;
- nonstationarity;
- confounding.

A laboratory that only succeeds on the first world is not general.

## Locked batch matrix

```bash
python -m behavior_lab batch-stress \
  --data-dir runs/batch \
  --worlds habit,two_mode,threshold,nonstationary,confounded \
  --seeds 11,23,47,89,131 \
  --episode-counts 100,300,1000
```

Each world/seed/sample-size job has its own specification hash, ledger, lock file, start record, and terminal record. Re-running an expanded matrix skips already completed identical jobs and runs only new jobs.

## Test suite

```bash
python -m pytest
python -m unittest discover -s tests -q
python -m compileall -q src tests examples
```

The adversarial tests cover:

- concurrent ledger appends;
- world restart continuity and configuration pinning;
- conflicting split assignments;
- hidden budget persistence and campaign-renaming attacks;
- empty prospective evaluation before future collection;
- frozen artifact and training-snapshot consistency;
- duplicate experiment outcomes;
- preregistration trial limits;
- tampered assignment payloads;
- canonical provenance protection;
- variable-propensity treatment comparison;
- non-finite predictions;
- formula resource limits and function arity;
- semantic model-artifact corruption;
- stale run-lock recovery;
- malformed LLM proposals;
- target and feature validation;
- hypothesis-lineage ID stability.

## Important limitation

Hidden labels are not directly returned, but aggregate scores inevitably carry statistical information. A constant predictor can, in principle, reveal something about aggregate label prevalence from a proper scoring rule. The default one-query lockbox limits adaptive probing; it does not provide differential privacy or cryptographic secrecy.
