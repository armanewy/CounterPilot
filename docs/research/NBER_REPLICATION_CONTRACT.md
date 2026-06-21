# NBER Replication Contract

Wave 1 Prompt 1C freezes the published-result replication targets for the
NBER eBay Best Offer release before full normalization or model training. The
objective is external evidence about OfferLab, not platform expansion.

The machine-readable contract is
`datasets/manifests/nber_replication_targets.yaml`. That file is intentionally
JSON-subset YAML so the standard library can validate it without adding a YAML
dependency.

## Scope

- Dataset: NBER Best Offer Sequential Bargaining.
- Required tables: `anon_bo_lists.csv` and `anon_bo_threads.csv`.
- Source cache: `C:\OfferLabData\source_docs`.
- Raw data policy: raw `anon_bo_lists.csv.gz`, `anon_bo_threads.csv.gz`, and
  `bargaining_data.zip` were not downloaded for this contract.
- Gate rule: every fatal structural invariant and fatal published descriptive
  moment must reproduce within tolerance before model training. Nonfatal
  diagnostics must be reported and triaged, but they do not alone block the main
  ingestion.

## Source Artifacts

| ID | Official source | Local cached artifact | SHA-256 |
| --- | --- | --- | --- |
| `nber_dataset_page` | https://www.nber.org/research/data/best-offer-sequential-bargaining | `C:\OfferLabData\source_docs\best-offer-sequential-bargaining.html` | `546BE9EC06750E04E973DB6CA02703711554A96FC98AE838DAFFC3A16E05154D` |
| `nber_appendix_listing` | http://www.nber.org/data-appendix/w24306/bargaining | `C:\OfferLabData\source_docs\data-appendix-w24306-bargaining.html` | `DB01C80EFF129D4E451F4799EC4A1394E5691123EE69D24F4F2954CFEFF62EAA` |
| `nber_codebook` | https://nber.org/bargaining/Codebook.xlsx | `C:\OfferLabData\source_docs\Codebook.xlsx` | `3FA5E83046AC29E610CF2BCF02FD85682F93F3608C689C5A434D794C65BB6516` |
| `nber_readme_pdf` | http://www.nber.org/data-appendix/w24306/bargaining/README.pdf | `C:\OfferLabData\source_docs\README.pdf` | `B7C1DE64087C77ECC88A7746299EAD5642FBBBD62AA2618815A937866B9E4246` |
| `nber_working_paper_pdf` | https://www.nber.org/system/files/working_papers/w24306/w24306.pdf | `C:\OfferLabData\source_docs\w24306.pdf` | `5E12E4DAC6DE91570013B6AC34B8757D17237DF783ABB58A6E40D23B77A1227B` |
| `released_code_zip` | https://nber.org/bargaining/sequential_bargaining_QJE_code.zip | `C:\OfferLabData\source_docs\sequential_bargaining_QJE_code.zip` | `94790B8638A0BE5D96807A1D09E970BBE7C2A8282FC91B95838F1220CA6D882E` |
| `paper_sample_do` | released code ZIP: `paper_sample.do` | `C:\OfferLabData\source_docs\paper_sample.do` | `9AFD57C2DC711EB00B6709CA4A6CAB335505EC6B3966E1F76EF4BCA16A9D3F97` |
| `summary_stats_main_do` | released code ZIP: `summary_stats_main.do` | `C:\OfferLabData\source_docs\summary_stats_main.do` | `56FD56F3F0D5DB82970BEBF2AF6C698C43D345CB63A357BF172DFF75F8C81184` |
| `summary_stats_ref_do` | released code ZIP: `summary_stats_ref.do` | `C:\OfferLabData\source_docs\summary_stats_ref.do` | `995B3A1B782CC946887D8D6201B8630D004ADB11721FABA307F9D12563BD2CE3` |
| `game_tree_do` | released code ZIP: `game_tree.do` | `C:\OfferLabData\source_docs\game_tree.do` | `20DAFFC2F5727012703B5832750FEFE368B51CFB25036EE54DCB923F5245C80B` |

## Codebook Commitments

- `anon_bo_lists.csv` is the listing table; `anon_bo_threads.csv` is the offer
  and counteroffer table.
- Join listings to threads on `anon_item_id`.
- A bargaining thread is `anon_item_id` plus `anon_byr_id`.
- Accepted statuses are `status_id in {1, 9}`.
- A countered offer is `status_id == 7`.
- Buyer offer types are `offr_type_id in {0, 1}` and seller counteroffers are
  `offr_type_id == 2`.
- Used condition is `item_cndtn_id >= 3000` when `item_cndtn_id` is nonmissing.
- The reference-price sample requires `count4` nonmissing, `count4 >= 20`, and
  `item_cndtn_id` nonmissing.

## Exclusion Policy

The published Appendix A sample restrictions are listing-level restrictions:

- L1: listing price at or below USD 1000.
- L2: sale price at or below listing price when a sale occurs.
- T1: all offers at or below listing price.
- T2: neither buyer nor seller makes more than three offers.
- T3: every countered offer has a counteroffer in the dataset.
- T4: every accepted offer ends the thread.

Released 2019 `paper_sample.do` adds T5, a duplicate timestamp exclusion at
`anon_item_id`, `anon_byr_id`, `src_cre_date`. Appendix A Table A1 does not
publish T5. A normalizer must therefore report both the Appendix A-compatible
counts and the released-code T5 delta if T5 changes the sample.

## Target Registry

| Target ID | Level | Status | Source/table | Population and formula | Expected and tolerance | Known exclusion or comparison difference |
| --- | --- | --- | --- | --- | --- | --- |
| `struct_raw_listings_before_restrictions` | Structural invariant | Fatal | Appendix A Table A1; `paper_sample.do` | All released listings before restrictions; count distinct `anon_item_id`. | `98,307,281`; exact. | Dataset page rounds this as 98 million; use Table A1 for exact count. |
| `struct_main_sample_listings_after_restrictions` | Structural invariant | Fatal | Appendix A Table A1; `paper_sample.do` | Listings with published L1-L2 and T1-T4 flags summing to zero. | `88,388,220`; exact. | Released code adds T5; report T5 separately if nonzero. |
| `struct_l1_price_over_1000_exclusions` | Structural invariant | Fatal | Appendix A Table A1 row L1 | `sum(start_price_usd > 1000)`. | `9,547,987`, fraction `0.0971`; exact count, fraction tolerance `0.00005`. | L1 is the large arbitrary price cap, about 10 percent of listings. |
| `struct_l2_sale_price_above_listing_exclusions` | Structural invariant | Fatal | Appendix A Table A1 row L2 | `sum(item_price > start_price_usd and item_price is not missing)`. | `42,524`, fraction `0.000433`; exact count, fraction tolerance `0.0000005`. | Paper treats these as abnormal, possibly bundled, sales. |
| `struct_t1_offer_above_listing_exclusions` | Structural invariant | Fatal | Appendix A Table A1 row T1 | Listing-level max of `offr_price > start_price_usd`. | `386,096`, fraction `0.00393`; exact count, fraction tolerance `0.000005`. | Many cases can follow seller price revisions because only final listing price is observed. |
| `struct_t2_offer_limit_exclusions` | Structural invariant | Fatal | Appendix A Table A1 rows T2 buyer/seller | Buyer: max thread buyer-offer count above 3. Seller: max seller-offer count above 3. | Buyer `3,529`, `0.0000359`; seller `0`, `0`; exact counts. | Sample period used a three-offer cap; eBay later moved to five offers in 2017. |
| `struct_t3_t4_sequence_integrity_exclusions` | Structural invariant | Fatal | Appendix A Table A1 rows T3/T4 | T3: countered offer lacks next counterparty counter. T4: accepted offer is not final. | T3 `1,453`, `0.0000148`; T4 `1,111`, `0.0000113`; exact counts. | These are rare processing-error exclusions; T5 is separate in released code. |
| `pub_table1_listing_price_mean` | Published descriptive moment | Fatal | Table 1 Listing-Level Data | Main sample; `mean(start_price_usd)`. | `94.6`; tolerance `0.05`. | Raw listings above USD 1000 are excluded by L1. |
| `pub_table1_listing_used_rate` | Published descriptive moment | Fatal | Table 1 Listing-Level Data and note | Main sample with nonmissing condition; `mean(item_cndtn_id >= 3000)`. | `0.548` over `60,709,655`; value tolerance `0.0005`, denominator exact. | Condition is missing outside that denominator. |
| `pub_table1_listing_revised_rate` | Published descriptive moment | Fatal | Table 1 Listing-Level Data | Main sample; `mean(bin_rev)`. | `0.263`; tolerance `0.0005`. | Explains part of T1 because current listing price may be post-revision. |
| `pub_table1_listing_sold_rate` | Published descriptive moment | Fatal | Table 1 Listing-Level Data | Main sample; `mean(item_price is not missing)`. | `0.215`; tolerance `0.0005`. | Includes Buy-It-Now and Best Offer sales. |
| `pub_table1_listing_sold_best_offer_rate` | Published descriptive moment | Fatal | Table 1 Listing-Level Data | Main sample; `mean(bo_ck_yn == 1)`. | `0.132`; tolerance `0.0005`. | Listing-level rate, not conditional on sale. |
| `pub_table1_listing_received_offer_rate` | Published descriptive moment | Fatal | Table 1 Listing-Level Data | Main sample; merge distinct thread listing IDs; `mean(thread_merge == 3)`. | `0.206`; tolerance `0.0005`. | Narrative rounds this as 25.4 million listings receiving offers. |
| `pub_table1_listing_sale_price_to_list` | Published descriptive moment | Fatal | Table 1 Listing-Level Data | Main sample sold listings; `mean(item_price / start_price_usd)`. | `0.832`; tolerance `0.0005`. | Includes Buy-It-Now and Best Offer sales. |
| `pub_table1_listing_bargained_price_to_list` | Published descriptive moment | Fatal | Table 1 Listing-Level Data | Main sample Best Offer sales; `mean(item_price / start_price_usd where bo_ck_yn == 1)`. | `0.727`; tolerance `0.0005`. | Paper narrative rounds this to 73 percent. |
| `pub_table1_seller_count_and_feedback_denominator` | Published descriptive moment | Fatal | Table 1 Seller-Level Data and note | Main sample collapsed by `anon_slr_id`; count sellers and nonmissing feedback. | Sellers `1,197,419`; feedback sellers `1,145,426`; feedback mean `99.4`; counts exact, mean tolerance `0.05`. | Paper narrative appears to swap buyer/seller counts; Table 1 and code control. |
| `pub_table1_buyer_count` | Published descriptive moment | Fatal | Table 1 Buyer-Level Data | Main sample buyer panel; count distinct `anon_buyer_id`. | `4,701,455`; exact. | Paper narrative appears to swap buyer/seller counts; Table 1 and code control. |
| `pub_table1_thread_count` | Published descriptive moment | Fatal | Table 1 Thread-Level Data | Main sample first offers by `anon_item_id`, `anon_byr_id`; drop `offr_price / start_price_usd > 1`; count rows. | `25,458,516`; exact. | Figure 4 root count is smaller because game-tree path construction differs. |
| `pub_table1_thread_offer_count_mean` | Published descriptive moment | Fatal | Table 1 Thread-Level Data | Same thread panel; `mean(rounds)`, excluding listing price as an offer. | `1.66`; tolerance `0.005`. | Counts rows in `anon_bo_threads`, not the seller's listing price. |
| `pub_table1_thread_agreement_rate` | Published descriptive moment | Fatal | Table 1 Thread-Level Data | Same thread panel; `mean(max(status_id in {1, 9}) by thread)`. | `0.454`; tolerance `0.0005`. | Agreement is accepted or auto-accepted offer, not outside-thread Buy-It-Now. |
| `pub_table1_thread_first_offer_to_list` | Published descriptive moment | Fatal | Table 1 Thread-Level Data | Same thread panel; `mean(first offr_price / start_price_usd)`. | `0.608`; tolerance `0.0005`. | First buyer offer dollar mean is `86.6`; ratio is the more normalization-stable target. |
| `diag_ref_sample_listing_count` | Nonfatal diagnostic moment | Nonfatal | Appendix B Table B1 | Reference-price sample: `count4` nonmissing, `count4 >= 20`, condition nonmissing, main restrictions; count listings. | `2,047,079`; exact. | Excludes one-of-a-kind listings; blocks reference analyses only. |
| `diag_ref_sample_sold_rate` | Nonfatal diagnostic moment | Nonfatal | Appendix B Table B1 | Reference-price sample; `mean(item_price is not missing)`. | `0.467`; tolerance `0.0005`. | Higher than main sample sale rate because sample is cataloged/comparable goods. |
| `diag_ref_sample_received_offer_rate` | Nonfatal diagnostic moment | Nonfatal | Appendix B Table B1 | Reference-price sample; `mean(thread_merge == 3)`. | `0.427`; tolerance `0.0005`. | Not comparable to main sample offer rate of `0.206` because reference sample is selected. |
| `diag_ref_sample_bargained_price_to_list` | Nonfatal diagnostic moment | Nonfatal | Appendix B Table B1 | Reference-price sample Best Offer sales; `mean(item_price / start_price_usd where bo_ck_yn == 1)`. | `0.813`; tolerance `0.0005`. | Appendix B notes less room to bargain than in the main sample. |
| `diag_ref_sample_thread_agreement_rate` | Nonfatal diagnostic moment | Nonfatal | Appendix B Table B1 | Reference-price sample thread panel; `mean(max(status_id in {1, 9}) by thread)`. | `0.25`; tolerance `0.0005`. | Lower than main sample agreement rate of `0.454`. |
| `diag_figure4_root_game_tree_count` | Nonfatal diagnostic moment | Nonfatal | Figure 4; `game_tree.do` | Main sample game-tree paths; root is `off[1,1]`. | `25,117,275`; exact. | Not the Table 1 thread count; nonfatal unless reproducing Figure 4. |
| `diag_figure4_first_seller_response_shares` | Nonfatal diagnostic moment | Nonfatal | Figure 4; `game_tree.do` | Root game-tree node; first seller response accept, counter, or decline. | Accept `0.33`, counter `0.27`, decline `0.40`; tolerance `0.005`. | Figure labels are integer percentages; decline includes expired, declined, auto-declined, and other-buyer-accepted statuses. |

## Leakage Risks

- These are aggregate replication targets, not predictive labels. They must be
  checked before any split-aware model training and must not be optimized against
  during model selection.
- The fatal thread targets use final thread outcomes, so they are validation-only
  aggregates. They must not enter pre-decision feature rows.
- Reference-price diagnostics use constructed prices from external fixed-price
  listings. Those diagnostics are useful for ingestion QA, but reference prices are
  not automatically approved as production features.
- Seller and buyer identifiers are needed to reproduce published aggregates, but
  identifier-derived history features require separate leakage-safe split checks.

## Limitations

- This contract does not process raw CSVs and does not implement normalization.
- Some values are rounded in the published tables; tolerances reflect published
  rounding, not raw-data uncertainty.
- The NBER working paper text and Table 1 appear to disagree on buyer versus
  seller counts. The contract follows Table 1 and released code.
- Appendix A publishes L1-L2 and T1-T4, while released 2019 code adds T5. The
  first full normalization must report the T5 delta explicitly.
- The source is public research evidence for eBay bargaining behavior. It does
  not support claims about modern seller profit lift, causal treatment effects, or
  production OfferLab weights.
