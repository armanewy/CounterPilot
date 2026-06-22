# NBER Full Normalization Report

Status: official full-source normalization completed on 2026-06-22. The
normalization and partition-integrity checks passed. The full-release evidence
gate remains closed because the frozen published-stat replication contract did
not pass and an independent pass audit is not available.

## Run

```powershell
python -m behavior_lab nber-best-offer normalize-real --raw-dir C:\OfferLabData\raw\nber_best_offer --output-dir C:\OfferLabData\processed\nber_best_offer_full --full --resume
python -m behavior_lab nber-best-offer replication-check --normalized-dir C:\OfferLabData\processed\nber_best_offer_full
python -m behavior_lab nber-best-offer finalize-evidence --normalized-dir C:\OfferLabData\processed\nber_best_offer_full --independent-audit-artifact C:\OfferLabData\processed\nber_best_offer_full\independent_audit.json
```

## Output

| Item | Value |
| --- | ---: |
| Negotiation-turn rows | 47,375,804 |
| Negotiation-turn partitions | 948 |
| Thread-linked listing rows | 19,546,308 |
| Listing partitions | 391 |
| Unmatched listing IDs | 0 |
| Duplicate full thread rows removed | 1,396 |
| Distinct author-code thread groups (`anon_item_id`, `anon_byr_id`) in replication DB | 28,202,940 |
| Manifest SHA-256 | `5D4A7C0E0F7B424DA7FDE27FF9537BFE6ED5C88E04579381987F4DED831BD832` |
| Normalization payload hash | `7D1BE0CEAB893AD1AB5B178C13E45D84A661F2C6C15149E62C1801FBBD56F7B7` |

Official source hashes and byte sizes matched the frozen source contract:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `anon_bo_lists.csv.gz` | 4,451,661,738 | `CEDA12755878304DAA4CA43B45C72EC98A7382A1EE646E66C33F6841E5D1A646` |
| `anon_bo_threads.csv.gz` | 1,374,076,192 | `F6FAEB797A8ED2F0C84D0E3C6E9B82F0AD2BD971DF354D57C902B478E757DEE9` |

## Artifacts

- Manifest: `C:\OfferLabData\processed\nber_best_offer_full\manifest.json`
- Manifest hash file: `C:\OfferLabData\processed\nber_best_offer_full\manifest.json.sha256`
- Full replication check: `C:\OfferLabData\processed\nber_best_offer_full\replication_check.json`
- Full replication summary: `C:\OfferLabData\processed\nber_best_offer_full\_replication\full_replication_summary.json`
- Finalization report: `C:\OfferLabData\processed\nber_best_offer_full\finalize_evidence_report.json`
- Failed audit artifact: `C:\OfferLabData\processed\nber_best_offer_full\independent_audit.json`

## Evidence Gate

Passed checks:

- Full unbounded command mode.
- Disk preflight.
- Official source contract.
- Source files reverified from disk.
- Full-run checkpoint.
- Partition hashes and current partition integrity.
- Streaming full-run gate.

Blocking checks:

- `replication_contract_passed`
- `replication_artifact_verified`
- `independent_audit_passed`
- `independent_audit_artifact_verified`
- `declared_gate_passed`

The replication artifact evaluated all fatal targets, so this is not a missing
implementation state. It is a failed exact replication state.

## Fatal Replication Failures

Seven fatal targets failed under the frozen exact contract:

- `struct_main_sample_listings_after_restrictions`: observed `88,388,279`; expected `88,388,220`.
- `struct_t2_offer_limit_exclusions`: observed buyer count `3,518`; expected `3,529`; seller count matched `0`.
- `struct_t3_t4_sequence_integrity_exclusions`: observed missing-counter count `1,420` vs `1,453`; accepted-not-last count `1,089` vs `1,111`.
- `pub_table1_listing_used_rate`: rate within tolerance, but nonmissing denominator observed `60,709,702`; expected `60,709,655`.
- `pub_table1_seller_count_and_feedback_denominator`: observed seller count `1,197,420`; expected `1,197,419`; feedback denominator observed `1,145,427`; expected `1,145,426`.
- `pub_table1_buyer_count`: observed `4,701,453`; expected `4,701,455`.
- `pub_table1_thread_count`: observed `25,458,645`; expected `25,458,516`.

Fourteen fatal targets passed, including raw listing count, L1, L2, T1,
listing price mean, revised rate, sold rates, received-offer rate, sale/list
ratios, mean offers, agreement rate, and first-offer/list ratio.

## Interpretation

The completed run proves the full-source streaming normalizer can process the
official NBER release with verified source files, resume checkpoints, partition
hashes, and lineage. It does not yet prove full-release benchmark readiness.

The observed failures are small but exact-count material. They are concentrated
around sample-restriction boundaries and participant counts, especially the
interaction between released-code duplicate handling and Appendix/Table target
definitions. The correct next step is to audit the target contract against the
released Stata code and paper appendix before allowing Benchmark v2 to consume
this full manifest.
