# Behavior Discovery Lab

Behavior Discovery Lab is a local, executable MVP for the four-wave research infrastructure described in the prompt:

1. **World Gym**: hidden synthetic behavioral worlds, append-only event ledger, temporal firewall, and blind evaluation.
2. **Formula Forge**: safe hypothesis DSL, logistic/rule/tree/state-style models, model comparison, Pareto frontier, residuals, counterexamples, and lineage.
3. **Personal N-of-1 Lab**: randomized intervention assignment, preregistration, crossover trials, treatment-effect estimation, and prospective model freezing.
4. **Autonomous Discovery Loop**: offline hypothesis generation, fitting, mutation, experiment proposal, observation consumption, and retire/promote decisions.

The core has no runtime dependencies beyond Python's standard library.

## Quick Start

```powershell
cd C:\Users\aoztu\Downloads\BehaviorDiscoveryLab
python -m behavior_lab demo --data-dir .demo --episodes 180 --iterations 3
python -m unittest discover -s tests
```

The demo seeds a hidden synthetic world, fits a heterogeneous model zoo, evaluates it through the blind judge, preregisters a randomized micro-experiment, estimates treatment effects from simulated trials, and runs an autonomous offline discovery loop.

## CLI

```powershell
python -m behavior_lab demo
python -m behavior_lab seed-world --data-dir .behavior_lab --world habit --episodes 200
python -m behavior_lab run-loop --data-dir .behavior_lab --iterations 5
python -m behavior_lab verify-ledger --data-dir .behavior_lab
```

## Design Notes

- The event ledger is append-only JSONL with a hash chain. Edits are represented as new facts, not rewrites.
- The temporal firewall builds prediction snapshots from pre-decision fields only.
- Hidden and prospective evaluation do not expose labels or failure rows.
- Real intervention launch paths require explicit approval; offline synthetic experiments do not.
- Hypotheses are executable artifacts with stable IDs, parent lineage, assumptions, falsification conditions, and counted complexity.
- `ResearchAPI` is the LLM-facing facade for schema inspection, training-data queries, hypothesis submission, fitting, evaluation, residual inspection, model comparison, experiment proposal, simulation, and frozen-candidate submission.
