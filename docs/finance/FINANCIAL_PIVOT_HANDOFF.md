# Financial Pivot Handoff

Wave: `FINANCE_WAVE_0`

Completed OfferLab evidence base commit: `e486609b48bfb1aaeafe6be64a01127cd8d6fe22`

Exact base commit for subsequent waves: resolve from the `HEAD_COMMIT` recorded
in `docs/audits/FINANCE_WAVE_0_AUDIT.json` from the first independent
`FINANCE_WAVE_0` audit whose verdict is `PASS`.

Minimum handoff artifact commit:
`4190ff20aa6368423970072760bf7ccbadac9e6b`

Latest remediated implementation commit before audit:
`1465b66ec769f563eb0f017574dcb879ae217281`

The completed OfferLab evidence base above is the pre-wave audited code state.
Git commits cannot contain their own final hash, so the audit-passed base for
subsequent waves is resolved from the independent PASS audit record rather than
embedded self-referentially in this file.

## Objective

Increase cumulative conservative verified value while minimizing capital,
research cost, maintenance, and human attention.

This is a private financial decision laboratory. It is not a general
correlation engine and not an individual-stock predictor.

## Action Boundary

All financial and seller actions remain manual. The system may produce paper
decisions and order-ticket-shaped artifacts in later waves, but this handoff
does not submit trades, seller actions, offers, purchases, inventory orders, or
notifications.

## Evidence Classes

Every handoff result is labeled as exactly one of:

- `public_research_evidence`
- `private_seller_historical_evidence`
- `prospective_shadow_evidence`
- `realized_commercial_evidence`

NBER-derived artifacts are `public_research_evidence` only. They must never be
treated as commercial evidence. Historical seller-policy comparisons are
descriptive only and must not be described as causal lift.

## Benchmark v1

Status: frozen and hidden-spent.

The frozen manifest is `reports/offerlab_benchmark_v1_final_manifest.json`.
Benchmark v1 cannot be rerun for model selection, cannot reuse hidden cases,
and cannot support production export or a commercial claim.

Evidence class: `public_research_evidence`.

## Benchmark v2

Status: implementation-ready but gated and not executed from tracked artifacts.

The integration path is implemented in
`src/behavior_lab/offerlab_models/benchmark_v2_integration.py`, with tests in
`tests/test_offerlab_benchmark_v2_integration.py`.

Benchmark v2 may run only when every preregistered prerequisite passes:

- audited full-release NBER normalization
- external Benchmark v1 hidden-token exclusion evidence
- fresh split and lockbox construction
- preregistered pre-hidden selection
- no hidden results used for selection

No tracked Benchmark v2 result is present in this repository.

Evidence class: `public_research_evidence`.

## Seller Pilot

Status: implementation-ready, data needed.

The seller pilot kit supports local template generation, import into an
external ledger, read-only mature-margin audit, and a read-only seller shadow
report. It preserves fees, shipping, cost basis, returns, cancellations, and
mature outcome semantics. Unknown material costs block net-profit claims.

No real seller pilot data is loaded in this repository. Seller data may be used
commercially only when supplied or explicitly authorized by the seller, kept
outside the repository, and evaluated in read-only or paper-shadow mode until a
later approval wave.

Evidence classes:

- pilot import and mature-margin audit:
  `private_seller_historical_evidence`
- shadow report:
  `prospective_shadow_evidence`

Historical seller-policy comparisons must remain descriptive and non-causal.

## eBay Feasibility

Status: not run.

The tracked feasibility report is
`reports/ebay_production_feasibility.json`. It is blocked by missing authorized
production token and missing manually supplied listing IDs. The next allowed
step is an operator-run production-only read-only probe. Crawling, broad
discovery, raw payload retention, and mutation endpoints remain disallowed.

Evidence class: `private_seller_historical_evidence`.

## Supportable Now

- research-only Benchmark v2 gated execution
- local seller pilot template generation
- local seller pilot import into an external ledger
- read-only mature-margin audit
- read-only seller shadow report
- operator-run read-only eBay feasibility probe

## Not Supportable Now

- real seller recommendations
- automated seller actions
- trade execution
- inventory purchases
- notifications
- production model export
- causal profit lift claims

## Unresolved Blockers

- No MoneyLedger or shared financial decision contract exists yet.
- No real seller pilot data has been imported into an external ledger in this
  repository.
- Benchmark v2 has no tracked full-release result.
- The authorized eBay production feasibility probe has not run.
- No Weather Edge, ETF risk, or financial-data semantics layer exists in this
  handoff wave.
