# NBER Final Publication Contract

This file pins the final-publication replication contract for the NBER Best
Offer Sequential Bargaining data. It is intentionally separate from the older
project replication contract, which captured the earlier no-T5 working-paper
interpretation.

## Authority

The final released analysis code is authoritative for executable sample
construction. The final online appendix and final QJE publication define the
publication target, while the 2018 NBER working paper is kept only as a
version-difference source.

Local released-code evidence:

| Source | Local path | SHA-256 |
| --- | --- | --- |
| Sample construction | `C:\OfferLabData\source_docs\paper_sample.do` | `9AFD57C2DC711EB00B6709CA4A6CAB335505EC6B3966E1F76EF4BCA16A9D3F97` |
| Table I code | `C:\OfferLabData\source_docs\summary_stats_main.do` | `56FD56F3F0D5DB82970BEBF2AF6C698C43D345CB63A357BF172DFF75F8C81184` |
| 2018 NBER working paper | `C:\OfferLabData\source_docs\w24306.pdf` | `5E12E4DAC6DE91570013B6AC34B8757D17237DF783ABB58A6E40D23B77A1227B` |

## Final Targets

| Target | Final-publication value |
| --- | ---: |
| Source listings | 98,307,281 |
| Final retained listings | 88,386,471 |
| Sellers | 1,197,397 |
| Buyers | 4,701,301 |
| Listing-buyer bargaining threads | 25,453,072 |
| T2 buyer violations | 3,518 |
| T2 seller violations | 0 |
| T3 violations | 1,451 |
| T4 violations | 1,109 |
| T5 duplicate timestamp violations | 4,273 |
| Missing Used values | 27,678,157 |
| Sellers with missing feedback | 51,992 |

## Version Differences

| Topic | Final 2020 publication and released code | Earlier 2018 working paper or legacy project target | Resolution |
| --- | --- | --- | --- |
| L1 | Exclude `start_price_usd > 1000`. | Some prose says `$0.99 <= price <= $1,000`. | `paper_sample.do` line 37 has only the upper-bound check. Do not impose a lower bound. |
| L2 | Exclude `item_price > start_price_usd` when sold. | No material conflict found. | Use `paper_sample.do` line 40. |
| T1 | Any offer above listing price invalidates the listing. | No material conflict found. | Use listing-level propagation from `paper_sample.do` lines 58-60. |
| T2 buyer | 3,518 listing-level violations. | 3,529. | Final contract uses 3,518. |
| T2 seller | 0 listing-level violations. | 0. | Values agree. |
| T3 | 1,451 listing-level violations. | 1,453. | Final contract uses 1,451. |
| T4 | 1,109 listing-level violations. | 1,111. | Final contract uses 1,109. |
| T5 | 4,273 listing-level violations. | No T5 in the older working-paper target set. | `paper_sample.do` lines 83-94 add `crit_duplicate_time` to `sample`. |
| Main sample listings | 88,386,471. | 88,388,220. | Final released-code sample includes T5 and final-publication denominator. |
| Missing-value denominators | Used missing = 27,678,157; feedback missing sellers = 51,992. | Legacy target manifest used no-T5 denominators. | Compute after final sample construction; missing stays missing. |

## Lower-Price Boundary

The lower-bound contradiction is resolved by the released code. Although prose
mentions `$0.99 <= listing price <= $1,000`, Table I reports a minimum listing
price of `$0.01`, and `paper_sample.do` constructs L1 only as:

```stata
gen crit_1k = (start_price_usd > 1000)
```

The implementation must not impose a `$0.99` lower-bound exclusion.

## Stale Values

The machine-readable stale-value report is in
`datasets/manifests/nber_final_publication_contract.json` under
`stale_repository_values`. The legacy values may remain only where they are
explicitly labelled as working-paper/no-T5 targets and excluded from the final
evidence gate.

Final gate inputs must use `final_publication_targets` exactly. No tolerance is
changed by this contract.
