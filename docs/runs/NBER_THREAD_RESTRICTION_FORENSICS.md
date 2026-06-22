# NBER Thread Restriction Forensics

This wave adds an executable T2-T5 reconstruction pass for the final NBER Best
Offer publication contract. It does not train models and does not adjust target
values.

The implementation streams raw `anon_bo_threads.csv` rows into deterministic
listing-buyer buckets, preserving `source_row_ordinal` as the secondary order
when `src_cre_date` ties. Each listing-buyer thread is evaluated independently,
then violations are propagated to the listing level.

Released-code semantics:

- T2 counts buyer price proposals where `offr_type_id in {0,1}` and seller
  price proposals where `offr_type_id == 2`; more than three by either party
  invalidates the listing.
- T3 flags a `status_id == 7` countered event with no following counterparty
  counteroffer.
- T4 flags an accepted event (`status_id in {1,9}`) that is not final in its
  listing-buyer sequence.
- T5 follows released `paper_sample.do`: `duplicates tag anon_item_id
  anon_byr_id src_cre_date`. Duplicate source events are not removed before
  this check.

Expected final-publication listing counts are held fixed:

| Rule | Expected listing count |
| --- | ---: |
| T2 buyer | 3,518 |
| T2 seller | 0 |
| T3 | 1,451 |
| T4 | 1,109 |
| T5 | 4,273 |

If observed counts differ, the manifest records a forensic mismatch. It does
not rewrite or relax the final-publication target.
