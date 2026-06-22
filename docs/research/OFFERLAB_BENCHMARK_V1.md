# OfferLab Benchmark v1

Status: historical, frozen, hidden-spent, and final STOP. Do not revise after hidden results, and do not run Benchmark v1 again. Any future full-release,
no-row-cap, new-split, new-control, calibration, or model result must be
versioned as Benchmark v2 or later.

## Scope

Benchmark v1 tests whether observable NBER Best Offer variables predict
negotiation outcomes better than strong simple baselines under leakage-safe
splits. It is research-only and non-exportable. It cannot support causal claims
about seller profit or production OfferLab automation.

The originally intended full-run path was the negotiation-first real NBER
normalization below. It is retained only as historical protocol context. After
v1 hidden access, this path must be implemented as Benchmark v2 or later, not
as a Benchmark v1 rerun:

1. Normalize official `anon_bo_threads.csv.gz`.
2. Retain only listings referenced by negotiation threads.
3. Verify raw hashes and normalized partition hashes.
4. Run the frozen NBER replication contract.
5. Build task rows without future fields, final outcomes, identifiers, or
   post-event participant-history fields inside model features.

## Targets

- `seller_next_action`: multiclass seller response after a buyer offer.
- `buyer_response_to_counter`: multiclass buyer response after a seller counter.
- `agreement`: binary thread-level agreement.
- `final_price_ratio`: regression target `final_sale_price / listing_price`
  for agreed Best Offer sales.
- `response_latency`: regression target `response_time - event_time`.

Unknown or censored rows are excluded from supervised targets and counted in
task reports. They are not converted to rejection.

## Splits

- Chronological listing-purged split, grouped by listing ID.
- Seller-disjoint split.
- Buyer-disjoint split where buyer IDs are available and stable enough to group.
- Category-disjoint diagnostic split.
- Thread-safe nested development split for model selection.
- One hidden lockbox per target.

The hidden lockbox may be queried once per target by the selected submission.
No protocol or feature changes may be made after hidden access.

## Baselines

- Overall class rate or median.
- Category rate.
- Offer-ratio threshold.
- Reference-price threshold is excluded from v1 unless a later protocol adds a
  separately proven as-of reference-price feature. The raw `ref_price4` field
  and normalized raw `reference_price` are excluded from Benchmark v1 predictors.
- Prior-concession heuristic.
- Split-the-difference heuristic.
- Regularized linear model.
- Small tree.

## Primary Metrics

- Classification: log loss, Brier score, accuracy, calibration, abstention, and
  relative log-loss improvement over the strongest simple baseline.
- Regression: MAE, RMSE, quantile loss, interval coverage, calibration, and
  abstention.
- All targets: support coverage, subgroup counts, chronological robustness,
  seller-disjoint robustness, and negative-control results.

## Feature Set

Allowed pre-decision features:

- category
- condition
- listing price
- current actor/action/amount
- offer-to-asking ratio
- round number
- prior observed turn count
- prior observed counter count
- event hour

Excluded fields:

- buyer, seller, listing, and thread identifiers
- raw event timestamps
- final status, final sale price, accepted price, decline price, auto-accept
  price, auto-decline price
- `status_id`, `response_time`, and any later/future round fields
- `ref_price4`, raw normalized `reference_price`, and derived reference fields
  unless a future version proves they were recomputed from information available
  before the decision
- sold-by-Best-Offer outcome flags

## Transformations

- Numeric missing values are represented through explicit encoder behavior, not
  silent source-level zero filling.
- Category strings are encoded inside the model pipeline.
- Event time may be reduced to hour-of-day only.
- No feature may be constructed from hidden labels, future rows, artifact names,
  output partition names, or full-thread terminal state.

## Model Selection

Model selection uses only the training and development splits. The selected
candidate for each target is the simplest model within a small tolerance of the
best development log loss or error. Ties favor compact formulas and models with
better calibration and higher support coverage.

Hidden-query budget: one submission per target.

Stopping rule: stop after all preregistered baselines, negative controls, and
existing inspectable models have run once. Do not tune against hidden results.

Economically interesting threshold: at least about 5% relative hidden log-loss
improvement over the strongest simple baseline on a core classification target,
with usable calibration and survival under chronological and seller-disjoint
evaluation. This is an engineering threshold, not a causal claim.

## Subgroup Analyses

- Category group.
- First round versus later rounds.
- Offer-to-asking ratio bands.
- Listing price bands.
- Seller-history availability.
- Buyer-history availability.
- U.S. versus non-U.S. flags only as diagnostics, not production targeting.

## Negative Controls

- Random labels.
- Future-status canary features.
- Accepted/final-price canary features.
- Identifier-memorization canaries.
- Random-row split inflation compared with listing/seller-safe splits.
- Same-timestamp ordering perturbation.
- Censoring converted to rejection.
- Hidden metadata leakage through filenames or artifact names.

## Reporting

Benchmark v1 reports separately by target. It must not produce a universal
aggregate score, production model, automated eBay action, or causal
counteroffer-effect claim.
