# NBER Full Listing Pass

This wave adds a dedicated bounded-memory listing pass over the complete
`anon_bo_lists.csv` source. It does not modify negotiation-turn normalization
and does not train models.

The pass writes a partitioned JSONL `listing_restrictions` table keyed by
`listing_id`. Each row records L1 and L2 flags immediately and leaves T1-T5 as
`null` until the thread-restriction forensics pass joins listing-level thread
flags.

Important implementation choices:

- Missing `item_cndtn_id` and `fdbk_pstv_start` stay missing; they are not
  coerced to zero.
- L1 uses only `start_price_usd > 1000`, matching released `paper_sample.do`.
  No `$0.99` lower-bound exclusion is imposed.
- Duplicate listing IDs are quarantined in `quarantine/duplicate_listing_ids.jsonl`.
  They are not silently deduplicated.
- Seller identifiers are stored as SHA-256 hashes, even though the source uses
  anonymized seller IDs.
- A complete manifest with matching signature and verified partition hashes is
  reused idempotently on rerun.

Commands:

```powershell
python -m behavior_lab nber-best-offer build-full-listing-restrictions --raw-dir C:\OfferLabData\raw\nber_best_offer --output-dir C:\OfferLabData\processed\nber_best_offer_full\listing_restrictions --require-official-sources
python -m behavior_lab nber-best-offer inspect-full-listing-restrictions --output-dir C:\OfferLabData\processed\nber_best_offer_full\listing_restrictions
```

The manifest records raw row count, listing-price distribution, L1/L2 counts,
Used numerator and nonmissing denominator, seller counts before/after L1-L2,
seller-feedback nonmissing denominators, source hashes, runtime, and peak
`tracemalloc` memory.
