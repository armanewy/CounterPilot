# OfferLab Evidence Decision

Decision: **STOP Benchmark v1; define Benchmark v2 and pursue a read-only seller pilot.**

## Basis

- Benchmark v1 gate status: `STOP`.
- Core hidden improvement: `0.0250`, below the `0.05` threshold.
- Core hidden support coverage: `0.37`, below the `0.80` threshold.
- Full-release evidence: `false`.
- Row cap disabled: `false`.
- Protocol splits complete: `false`.
- Negative controls passed: `false`.
- Calibration quality validated: `false`.
- eBay production feasibility: blocked and technically indeterminate.

## Capital Plan

- Capital at risk: `$0`.
- Expected offers: `0`.
- Stop-loss: `$0`.
- Maximum duration: until Benchmark v2 full NBER normalization, complete v2
  splits/controls, validated calibration/support coverage, and one read-only
  seller pilot path are complete.

## Explicit Non-Decisions

- Do not enter shadow mode.
- Do not run randomized self-funded offers.
- Do not build a public current-data observatory.
- Do not export NBER-derived production models.
- Do not create, accept, decline, counter, discount, message, or otherwise mutate eBay state.

## Next Measurement Work

1. Complete full NBER normalization or document an alternate authorized seller-data source.
2. Implement every Benchmark v2 split and negative control.
3. Validate calibration quality and support coverage with explicit thresholds.
4. Run the authorized read-only eBay sandbox and production probes with manual listing IDs.
5. Run Benchmark v2 with a fresh hidden lockbox that excludes every spent v1
   hidden case. Do not describe any future result as a repeat of Benchmark v1.
