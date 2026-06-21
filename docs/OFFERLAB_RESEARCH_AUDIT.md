# OfferLab Autonomous Research Audit

Wave 5 adds a bounded autonomous research harness for OfferLab research
artifacts. It is not a production negotiator and not a security sandbox for
malicious in-process code.

Guardrails:

- The agent sees only schema, allowed variables, training preview, and
  development summaries.
- Hidden rows are reserved and never inspectable through the typed API.
- Hidden submission is one-shot per campaign lockbox.
- Agents cannot execute code, mutate outcomes, change budgets, or claim
  causality from observational results.
- The scheduler enforces cycles, hypothesis count, mutation count, development
  evaluations, hidden submissions, model count, runtime, and formula complexity.
- Proposals, failures, retirements, promotions, hidden submissions, and run
  completion records are persisted to an append-only JSONL event store.
- NBER-derived artifacts remain research-only and production export remains
  blocked.

Residual limitation:

- This API is a scientific boundary, not a hostile-code sandbox. A real LLM
  should run out of process with typed RPC access only.
