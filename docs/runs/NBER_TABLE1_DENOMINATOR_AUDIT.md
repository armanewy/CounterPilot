# NBER Table I Denominator Audit

This wave adds an executable audit for the final Table I identities and
denominators. It reads the full replication SQLite artifact and computes the
final sample using `sample_with_t5 = 1`.

Audited levels:

- Listing level: retained listings, Used numerator, Used nonmissing
  denominator, missing Used count, Used rate.
- Seller level: retained sellers, feedback nonmissing sellers, feedback
  missing sellers, feedback mean over nonmissing sellers, listings per seller.
- Buyer level: released-code buyer denominator from buyer offer activity merged
  back to retained listings by buyer ID. Distinct retained thread-offer buyers
  are reported separately because they are not the final Table I denominator.
- Thread level: distinct listing-buyer pairs among retained listings, duplicate
  pairs, and malformed identifier threads.

Final exact targets:

| Target | Value |
| --- | ---: |
| Listings | 88,386,471 |
| Sellers | 1,197,397 |
| Buyers | 4,701,301 |
| Threads | 25,453,072 |
| Missing Used listing values | 27,678,157 |
| Sellers missing feedback | 51,992 |

The audit also emits a reconciliation waterfall and a restriction-overlap
matrix. Because rule violations overlap, final retained sample size is verified
from the union flag `sample_with_t5`, not by subtracting individual rule counts.
