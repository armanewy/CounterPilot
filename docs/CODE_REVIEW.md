# Code Review and Stress-Test Findings

This version was reviewed adversarially after the lockbox and batch-roadmap implementation.

## Critical issues found and fixed

### Hidden evaluation could be bypassed

Pairwise hidden comparisons and repeated aggregate queries could bypass the intended one-shot budget.

**Fix:** hidden/prospective reservations are append-only ledger records, consumed before evaluation. Pairwise comparison is limited to training/development. Hidden case overlap is rejected across renamed campaigns.

### Initial "prospective" cases were not prospective

The newest fraction of an existing dataset had been treated as prospective.

**Fix:** initial manifests contain no prospective cases. New pre-freeze observations become staging; only observations first assigned after a model freeze become prospective and carry that freeze ID.

### New observations could leak backward into training

Recomputing fractional splits after data growth could move later observations into an earlier campaign's training set.

**Fix:** campaign split assignments are immutable ledger facts. New observations cannot enter an existing training/development/hidden snapshot.

### Hidden-world truth was available through the researcher facade

A mechanism-scoring method exposed evaluator-only information.

**Fix:** `ResearchAPI.mechanism_score` refuses access. Synthetic truth remains in the trusted stress evaluator only.

### Fitted models were not reliably bound to a data cut

A freeze needed to identify the exact artifact, training snapshot, split snapshot, and ledger cut.

**Fix:** persisted artifacts are hashed and reloadable. Freezes require a valid campaign fit and record artifact, training, split, and ledger hashes.

### Experiment outcomes could be duplicated or detached from preregistration

**Fix:** assignment limits, comparison consistency, immutable assignment matching, target presence, outcome chronology, and one-outcome-per-assignment are guarded atomically at append time.

### Treatment comparisons mixed experiments

**Fix:** estimates may be filtered by preregistration ID. Variable assignment probabilities now use a transparent Hajek inverse-probability-weighted point estimate.

### Synthetic generation repeated after restart

**Fix:** ledger-producing world events use seed/namespace/event-index-derived random streams, and event indices resume from the ledger.

### The model zoo contained hidden-world-specific assumptions

**Fix:** the general foundry now ranks observed features generically. Synthetic known-driver probes remain explicitly evaluator-only.

### Formula and artifact surfaces accepted malformed values

**Fix:** the DSL enforces syntax, node, term, length, function-arity, and finite-value limits. Model artifacts are hashed and semantically validated by family.

### Local ledger and run locks were fragile

**Fix:** writes use sidecar exclusive locks, guarded atomic append, fsync, hash-chain verification, stale-lock recovery, and idempotent per-run batch hashes.

### Repeated experiments reused the same allocation stream

Two preregistrations with identical scientific designs could receive the same deterministic assignment sequence, correlating treatment with context order across repetitions.

**Fix:** each design receives a deterministic occurrence sequence recorded at preregistration. Clean first runs remain seed-reproducible, while repeated registrations use independent persisted randomization namespaces.

## Additional hardening

- Hidden output no longer directly returns prevalence or baseline lift.
- Manual hypotheses must match the active target and known campaign variables.
- LLM proposal shapes are validated rather than accepting strings as lists.
- Adapter provenance cannot overwrite canonical randomized contexts.
- Invalid assignment probabilities and inconsistent assigned propensities are rejected.
- Mutation IDs include lineage so repeated campaigns do not collide.
- Baseline models are no longer recorded as retired hypotheses.
- The personal-lab facade refuses to create a false artifact-free prospective freeze.

## Remaining trust boundary

This remains a local MVP. An untrusted LLM executing in the same process can import internal modules or read files directly. Production automation should isolate the researcher and expose typed RPC endpoints only.

## Validation performed

- Unit and adversarial test suites.
- Concurrent ledger appends.
- Ledger tampering and artifact tampering checks.
- Reopen/resume behavior.
- Hidden budget persistence and cross-campaign case-reuse rejection.
- True post-freeze prospective evaluation.
- Multi-world stress matrix.
- Expanded batch matrix with idempotent skips.
- End-to-end demo with hidden and prospective lockboxes.
