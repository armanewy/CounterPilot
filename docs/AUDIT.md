# Scientific and Engineering Audit

This audit describes the guarantees the current MVP actually provides. It is
intentionally stricter than the product language: a convincing research UI is
not evidence that a behavioral mechanism has been discovered.

## High-severity issues fixed

### Prospective evaluation was not truly prospective

The initial implementation placed the tail of an already-existing dataset into
`prospective`. That is a temporal holdout, not a prospective test.

The corrected policy is:

- A campaign initially assigns only training, development, and hidden cases.
- Observations first recorded before a freeze but after campaign creation become
  `staging` and cannot enter that campaign's fit.
- A candidate freeze is an append-only ledger record bound to a persisted model
  artifact, training snapshot, split snapshot, and ledger head.
- Only observations first assigned after that freeze become `prospective`, and
  each is tagged with the freeze ID it evaluates.

### Holdout budgets could be reset by changing campaign names

Hidden-query budgets are now scoped to the actual hidden case set, not merely a
caller-selected campaign ID. Reusing any previously exposed hidden cases is
rejected across campaigns.

Prospective budgets are scoped to a specific freeze and its post-freeze cases.

### Lower-level evaluation paths could bypass normal lockboxes

The researcher-facing API now refuses hidden/prospective pairwise comparison,
residual inspection, and direct prospective evaluation. Hidden evaluation is a
one-shot aggregate query; prospective evaluation requires a frozen artifact.

`BlindEvaluationServer` and ledger files still exist in the same process. The
facade prevents accidental leakage, not malicious code with filesystem access.
Run an untrusted LLM researcher in a separate process/container that receives
only the typed tool API.

### Frozen candidates were not strongly bound to executable artifacts

Model artifacts are now versioned, hashed, persisted, and reloadable. A freeze
records:

- artifact hash,
- training snapshot hash,
- split snapshot hash,
- ledger head before the freeze,
- model and campaign IDs.

Prospective submission verifies the current executable artifact still matches
the frozen artifact.

### Ledger operations became quadratic while seeding

Bulk seeding and split materialization now use one guarded scan and one fsync per
batch while preserving the hash chain. A sidecar exclusive lock prevents local
writers from constructing competing records from the same previous hash.

### Experimental assignment and outcome records were too weakly linked

Preregistrations now enforce fixed trial counts and stopping plans. Trial
outcomes must match the immutable assignment record's treatment, comparator,
context, probability, preregistration, and assignment time. Duplicate outcomes
and outcomes recorded before assignment are rejected.

### The synthetic world could change after a process restart

Synthetic event randomness is derived deterministically from world seed and
event index. The ledger also pins world name, seed, target, and subject. Opening
a populated run directory with a different configuration is rejected.

### The personal-lab freeze API was misleading

`PersonalLab` records decisions and randomized trials but does not fit/persist
model artifacts. It now refuses to create a fake freeze marker and directs the
caller to `ResearchAPI.freeze_candidate`, which can satisfy the complete
artifact-and-split contract.

## Current scientific contract

### Development data

May be inspected repeatedly. It is where hypotheses are mutated, residuals are
viewed, and model families are compared.

### Hidden data

One aggregate query per actual hidden case set. Labels, failure rows, prevalence,
and base-rate lift are redacted. Aggregate metrics still transmit information,
which is why repeated probing is prohibited.

### Prospective data

Must be recorded after a model freeze and tagged to that freeze. It cannot be
used to mutate the same candidate.

### Mechanism claims

Prediction and mechanism recovery are scored separately. The stress test reports:

- performance against predictive baselines,
- driver recall of the best formula actually discovered,
- a separately labeled formula-language expressivity probe,
- intervention-direction performance.

Variable-name recall is not proof of causal or mathematical equivalence.

## Remaining gaps

1. **The lockbox is not a hostile security boundary.** Same-process code can
   import internal classes or read files. Isolate an autonomous LLM behind an
   RPC/tool server before treating the lockbox as adversarial.
2. **No automatic LLM provider is included.** `LLMHypothesisGenerator` is a
   validated seam, not an autonomous scientist.
3. **Real personal-data ingestion is not implemented.** `PersonalLab` is a
   manual/instrumentation boundary; a versioned bridge into discovery campaigns
   remains future work.
4. **Causal analysis is deliberately basic.** Randomized intention-to-treat
   difference in means is useful for the MVP, but not a general causal-inference
   system.
5. **The DSL is intentionally small.** It cannot yet search rich latent-state,
   change-point, survival, or temporal program structures.
6. **Synthetic mechanism scoring is approximate.** Equivalent formulas can use
   different variables or representations. Future world gyms should expose a
   canonical intervention test suite in addition to hidden source truth.
7. **Data deletion and privacy governance are not implemented.** The append-only
   ledger is appropriate for synthetic research but needs encryption, consent,
   retention, and redaction design before sensitive real-world use.

## Recommended trust progression

A candidate should progress through:

1. beats uniform/base-rate/recent-rate baselines on development,
2. survives multiple seeds and sample sizes,
3. is selected before the one-shot hidden query,
4. is frozen with a reproducible artifact,
5. predicts genuinely post-freeze observations,
6. predicts the direction of an intervention not used for fitting,
7. remains competitive after complexity is considered.

Anything less is an interesting hypothesis, not a discovered law of behavior.
