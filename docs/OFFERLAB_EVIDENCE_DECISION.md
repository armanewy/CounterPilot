# OfferLab Evidence Decision

Decision: **2. Improve measurement and repeat benchmark.**

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
- Maximum duration: until full NBER normalization, complete Benchmark v1 splits/controls, validated calibration/support coverage, and authorized read-only eBay feasibility probes are complete.

## Explicit Non-Decisions

- Do not enter shadow mode.
- Do not run randomized self-funded offers.
- Do not build a public current-data observatory.
- Do not export NBER-derived production models.
- Do not create, accept, decline, counter, discount, message, or otherwise mutate eBay state.

## Next Measurement Work

1. Complete full NBER normalization or document an alternate authorized seller-data source.
2. Implement every frozen Benchmark v1 split and negative control.
3. Validate calibration quality and support coverage with explicit thresholds.
4. Run the authorized read-only eBay sandbox and production probes with manual listing IDs.
5. Repeat Benchmark v1 without the 500-row model cap.
