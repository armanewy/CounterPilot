# NBER Best Offer Real Schema Mapping

Wave 1 Prompt 1A scope: codebook and source-schema mapping only. This file maps
the official NBER Best Offer release schema to the repository's existing
fixture-sized benchmark contract. It does not change production normalization
code and does not use or commit raw NBER datasets.

## Sources Used

Downloaded source documents were stored outside the repository at
`C:\OfferLabData\source_docs`.

| Artifact | Official URL | SHA-256 |
| --- | --- | --- |
| `Codebook.xlsx` | `https://www.nber.org/bargaining/Codebook.xlsx` | `3FA5E83046AC29E610CF2BCF02FD85682F93F3608C689C5A434D794C65BB6516` |
| `sequential_bargaining_QJE_code.zip` | `https://www.nber.org/bargaining/sequential_bargaining_QJE_code.zip` | `94790B8638A0BE5D96807A1D09E970BBE7C2A8282FC91B95838F1220CA6D882E` |
| `best-offer-sequential-bargaining.html` | `https://www.nber.org/research/data/best-offer-sequential-bargaining` | `8A84006852B81266809817A4E07B08CFEAC772CD657FF5D948C9D4D4D3AF7A5A` |

Key extracted-code hashes and source probes:

| Extracted file | SHA-256 |
| --- | --- |
| `load_csv_files.do` | `17BD54A6B3B7CF7E689DE2062CAC4FA4E99B50F7743991477A48C57B8D2B9B67` |
| `paper_sample.do` | `9AFD57C2DC711EB00B6709CA4A6CAB335505EC6B3966E1F76EF4BCA16A9D3F97` |
| `summary_stats_main.do` | `56FD56F3F0D5DB82970BEBF2AF6C698C43D345CB63A357BF172DFF75F8C81184` |
| `competition_facts.do` | `6EEF2395D01889B7AA79BFD1EA1879BFEDE79A64FC091E61DDF1E8D58C4574DB` |
| `price_conv.do` | `1C0FA2FEF82049D3369ABD2AF12574ED583E4D8BB9F1D6E83CF901B8424D71DB` |
| `anon_bo_lists.csv.gz` ranged header probe | full file not downloaded; compressed-size total observed as 4,451,661,738 bytes |
| `anon_bo_threads.csv.gz` ranged header probe | full file not downloaded; compressed-size total observed as 1,374,076,192 bytes |

## Codebook Sheets

`Codebook.xlsx` contains two sheets:

| Sheet | Contents inspected |
| --- | --- |
| `bo_lists` | 38 listing-level variables plus value-code notes |
| `bo_threads` | 15 offer/counteroffer variables plus value-code notes |

The NBER data page describes `anon_bo_lists.csv` as 98 million eBay Best Offer
listings from May 1, 2012 to June 1, 2013 and `anon_bo_threads.csv` as the
offers and counteroffers corresponding to those listings.

## Extracted Headers

Verified raw order for `anon_bo_lists.csv`:

```text
anon_item_id,anon_title_code,anon_product_id,anon_leaf_categ_id,anon_slr_id,anon_buyer_id,auct_start_dt,fdbk_score_start,fdbk_pstv_start,auct_end_dt,start_price_usd,photo_count,to_lst_cnt,bo_lst_cnt,count1,ref_price1,count2,ref_price2,count3,ref_price3,item_cndtn_id,view_item_count,wtchr_count,meta_categ_id,item_price,bo_ck_yn,ship_time_slowest,ship_time_fastest,ship_time_chosen,decline_price,accept_price,bin_rev,lstg_gen_type_id,store,ref_price4,count4,slr_us,buyer_us
```

Verified raw order for `anon_bo_threads.csv`:

```text
anon_item_id,anon_thread_id,anon_byr_id,anon_slr_id,src_cre_dt,fdbk_score_src,fdbk_pstv_src,offr_type_id,status_id,offr_price,src_cre_date,response_time,slr_hist,byr_hist,any_mssg,byr_us
```

Official-source discrepancy: `load_csv_files.do` imports `anon_bo_threads.csv`
and converts `src_cre_dt`, `src_cre_date`, and `response_time` to Stata
dates/times. `src_cre_dt` is used by authors' code but omitted from the
codebook sheet. A ranged gzip probe of the official thread file confirms its
raw header position. The raw thread file also spells buyer location as `byr_us`,
while the codebook prose uses `buyer_us`.

## Identifier Mapping

| Entity | Source field(s) | Canonical mapping |
| --- | --- | --- |
| Listing | `anon_item_id` | `listing_id` |
| Seller | `anon_slr_id` | `seller_id`; split/group identifier only |
| Bargaining buyer | `anon_byr_id` | `buyer_id`; split/group identifier only |
| Actual purchaser | `anon_buyer_id` | post-outcome field; not a feature |
| Thread | `anon_thread_id`; authors often group `anon_item_id + anon_byr_id` | prefer `anon_thread_id`, audit against author grouping |
| Offer/turn | no explicit offer id | derive `turn_index` by sorting within thread by `src_cre_date`; tie-break with stable row ordinal |

## Offer and Outcome Semantics

`anon_bo_threads.csv` rows are both events and response-state records. The event
is the offer/counteroffer at `src_cre_date`; the response state is encoded by
`status_id` and `response_time`, which are future fields for pre-decision
snapshots.

`offr_type_id` maps to actor/action:

| Code | Meaning | Canonical |
| --- | --- | --- |
| `0` | initial buyer offer | buyer / offer |
| `1` | buyer counteroffer | buyer / counter |
| `2` | seller counteroffer | seller / counter |

`status_id` maps to response/outcome:

| Code | Meaning | Treatment |
| --- | --- | --- |
| `0` | offer expired after more than 48 hours | terminal negative / expiration |
| `1` | accepted | terminal agreement |
| `2` | declined | terminal negative |
| `6` | auto declined | terminal negative; auto-rule outcome |
| `7` | countered | nonterminal if followed by counter; censored if final observed row |
| `8` | declined because another buyer's offer was accepted | competing-buyer censoring, ambiguous for normal agreement labels |
| `9` | auto accepted | terminal agreement; auto-rule outcome |

There is no separate accept/decline event row. Acceptance, decline, expiration,
and competing-buyer loss are statuses attached to the offer being answered.

## Final Price

`item_price` in `anon_bo_lists.csv` is the final price. The codebook states that
it is the Buy-It-Now price for Buy-It-Now sales, the final negotiated price for
Best Offer sales, and missing if the item never sold. Use `bo_ck_yn == 1` to
filter Best Offer sales before constructing canonical `final_price_ratio =
item_price / start_price_usd`. `item_price` and `bo_ck_yn` are labels/future
fields, never pre-decision features.

`offr_price` is the current offer/counteroffer amount and maps to canonical
turn `amount`.

## Censoring and Ambiguity

Quarantine or task-filter these cases before supervised labels:

- final observed row has `status_id == 7`;
- `status_id == 8`, because another buyer won the listing;
- countered rows with no following expected counter (`paper_sample.do` T3);
- accepted rows that are not last (`paper_sample.do` T4);
- duplicate `anon_item_id + anon_byr_id + src_cre_date` (`paper_sample.do` T5);
- `anon_thread_id` conflicts with `anon_item_id + anon_byr_id`;
- sold-listing `auct_end_dt`, because the codebook says sale date replaces the
  chosen listing end date.

`price_conv.do` treats status `0` and `8` as declines and drops threads with no
agreement/disagreement for one figure. That is an analysis-specific rule, not a
global label policy.

## Leakage Risks

High-risk or forbidden predictor fields:

- Outcome/future fields: `item_price`, `bo_ck_yn`, `status_id`,
  `response_time`, `anon_buyer_id`, listing `buyer_us`, `ship_time_chosen`,
  sold-listing `auct_end_dt`, `bin_rev`.
- Policy leaks: `accept_price`, `decline_price`.
- Temporal-leak risks until recomputed or audited: `ref_price1`-`ref_price4`,
  `count1`-`count4`, `view_item_count`, `wtchr_count`, `to_lst_cnt`,
  `bo_lst_cnt`, `slr_hist`, `byr_hist`.
- Identifier memorization risks: `anon_slr_id`, `anon_byr_id`,
  `anon_title_code`, `anon_product_id`; use only for splits or after explicit
  memorization controls.

The complete per-variable machine-readable mapping is in
`datasets/manifests/nber_best_offer_real_mapping.yaml`.

## Fixtures

Synthetic fixtures were added under `tests/fixtures/nber_real_schema/`.
They include codebook field names, DMY/DMYhms date strings, blank missing
values, the `anon_product_id == 547957` sentinel from the authors' loader,
`status_id` examples for countered/accepted/expired/competing-buyer outcomes,
and missing `slr_hist`/`byr_hist` patterns. They contain no raw NBER rows.

## Gate Status

Prompt 1A gate: passed for codebook inspection, source-doc hashing,
machine-readable mapping, ranged raw-header verification,
identifier/event/outcome resolution, leakage notes, and synthetic fixtures.
The full raw datasets were not downloaded or committed.
