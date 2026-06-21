# Wave Audit Protocol

Every OfferLab evidence wave must pass an independent correctness audit before the next wave starts.

## Invariant

Do not let implementation convenience weaken these boundaries:

- Research-only datasets cannot produce production-exportable artifacts.
- No task may expose labels, final outcomes, future rounds, or future timing as pre-decision features.
- Split logic must match the claim being made: chronological, seller-disjoint, category-disjoint, or randomized as appropriate.
- Evaluator-validation datasets validate estimators only; they do not train eBay negotiation behavior.
- Causal-validation datasets validate treatment-effect machinery only; they do not imply OfferLab profit lift.
- Simulation and dialogue datasets stay labeled as simulation or language extraction.
- CLI commands and docs must not claim causal seller-profit improvement from retrospective or public data.
- Full external downloads require explicit user intent; generated samples and tests must remain tiny.
- eBay code must remain read-only unless a later explicit automation wave approves mutation.

## Required Audit Lanes

Run independent read-only agents after each wave:

1. **Permission Firewall**
   Check source registry, artifact lineage, and production-export refusal.

2. **Leakage And Splits**
   Check task builders, forbidden features, observed history, split contracts, and audit reports.

3. **Estimator Correctness**
   Check OPE, causal, calibration, frontier, and uncertainty math introduced by the wave.

4. **Scope And Claims**
   Check CLI commands and docs for overclaiming, unintended downloads, credential requests, production export, or eBay mutation.

Add more lanes when a wave introduces a new high-risk surface, such as LLM autonomy, live marketplace APIs, or self-funded inventory economics.

## Audit Output

Each audit report must lead with findings:

```text
Severity
File:line
Invariant violated
Why it matters
Minimal fix
```

If no findings exist, the report must still list residual risks.

## Gate

Do not start the next implementation wave until:

- All critical/high audit findings are fixed.
- Medium findings are either fixed or explicitly accepted with rationale.
- Tests and smoke commands pass after fixes.
- The post-audit commit is cleanly separated from the wave implementation when practical.
