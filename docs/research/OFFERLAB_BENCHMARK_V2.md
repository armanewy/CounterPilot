# OfferLab Benchmark v2

Status: defined protocol, not executed.

Benchmark v2 is a new benchmark. It is not a repeat of Benchmark v1. The v1
hidden lockboxes were queried, so every v2 hidden lockbox must be freshly
sampled and must exclude every v1 hidden case token before hidden access.

## Scope

Benchmark v2 tests whether pre-decision NBER Best Offer variables carry a
behavioral signal under full-release normalization, leakage-safe splits,
negative controls, and calibrated evaluation. It remains research-only. It
cannot establish seller contribution-margin lift, seller profit lift, or
production readiness.

Primary commercial-interest target:

- `seller_next_action`

Additional research targets:

- `buyer_response_to_counter`
- `agreement`
- `final_price_ratio`
- `response_latency`

## Non-Reuse Of V1

Benchmark v1 is permanently frozen with status:

- `frozen`
- `hidden_spent`
- `never_reusable_for_model_selection`

Any row-cap removal, new split, new control, calibration change, model change,
feature change, or full-release run after v1 hidden access belongs to Benchmark
v2 or later. It must not be described as a v1 rerun.

## Required Inputs

Benchmark v2 requires full official NBER Best Offer normalization:

1. Stream the complete official compressed source files.
2. Extract only listings referenced by negotiation threads.
3. Preserve raw hashes, schema versions, transformation versions, event time,
   and response time.
4. Quarantine malformed records with explicit reasons.
5. Preserve unknown and censored labels separately.
6. Produce deterministic partition manifests suitable for audit.

No model row cap is allowed.

## Splits

V2 requires all of these splits:

- Chronological listing-purged split.
- Seller-disjoint split.
- Buyer-disjoint split where identifiers permit.
- Category-disjoint diagnostic.
- Thread-safe nested development split.
- Fresh hidden lockbox.

Leakage constraints:

- A listing may not span train, development, or hidden regions in the
  chronological listing-purged split.
- Seller identifiers must be disjoint in the seller-disjoint split.
- Buyer identifiers must be disjoint in the buyer-disjoint split when present.
- Thread identifiers must not cross nested-development boundaries.
- V2 hidden case tokens must have zero overlap with all v1 hidden case tokens.

## Hidden Access

Hidden access is allowed only after the development-stage manifest proves:

- Full normalization is complete.
- All split manifests are immutable and hashed.
- Every required negative control has executed.
- Calibration thresholds are declared and validated on development.
- Support coverage is reported.
- One selected artifact per target has been frozen.
- The v2 hidden token set excludes all v1 hidden tokens.

There is exactly one hidden submission per target. No feature, model,
calibration, split, negative-control, or protocol change is allowed after hidden
access.

## Baselines And Models

Required baselines and models:

- Overall class rate or median.
- Category baseline.
- Offer-ratio heuristic.
- Prior-concession heuristic.
- Split-the-difference heuristic.
- Regularized linear or logistic model.
- Small tree.
- Existing compact formula candidates.

Model selection uses only training and nested development data. Hidden results
must not influence model choice.

## Calibration And Coverage

Classification calibration must report:

- Multiclass log loss.
- Brier score.
- Reliability bins.
- Expected calibration error.
- Classwise calibration.

The primary candidate for `seller_next_action` must cover at least 80% of
eligible hidden rows unless a selective-prediction objective was preregistered
before hidden access.

## Negative Controls

V2 includes every v1 negative control:

- Random labels.
- Future-status canary features.
- Accepted/final-price canary features.
- Identifier-memorization canaries.
- Random-row split inflation.
- Same-timestamp ordering perturbation.
- Censoring converted to rejection.
- Hidden metadata leakage through filenames or artifact names.

## Missing And Censored Labels

Unknown labels and censored outcomes must be represented explicitly in task
manifests. They may not be silently converted into rejections, failures, or
zero-margin outcomes.

## Possible Outcomes

The v2 gate may return:

- `STOP`
- `RESEARCH_SIGNAL`
- `READY_FOR_SELLER_SHADOW_VALIDATION`

It may never return production-ready based on NBER data.

