# OfferLab Dataset Roadmap

This roadmap separates evidence sources by the claim they are allowed to support. It intentionally avoids a single pooled training corpus.

## Source Roles

| Source | Role | Allowed claim | Not allowed |
| --- | --- | --- | --- |
| NBER eBay Best Offer | Direct evidence | Real eBay bargaining behavior is predictable under leakage-safe splits | Causal seller profit lift |
| Open Bandit Dataset | Evaluator validation | Off-policy estimators behave on logged randomized e-commerce data | eBay negotiation behavior |
| Criteo Uplift | Causal validation | Treatment-effect machinery handles randomized uplift data | Production OfferLab weights |
| AuctionNet | Simulation | Strategy code behaves in a strategic environment | Real buyer acceptance rates |
| CraigslistBargain | Language extraction | Offer and concession parsing from dialogue | Economic response probabilities |
| Current authorized eBay seller data | Commercial calibration | Seller-specific production evidence when authorized | Public crawling or unauthorized financial access |

## Wave 0 Findings

NBER is the core benchmark because it contains real eBay Best Offer listings and sequential negotiations. NBER describes 98 million listings from May 1, 2012 to June 1, 2013, with listing-level variables such as listing price, constructed reference prices, and category. It is direct behavioral evidence but still lacks seller acquisition cost, modern actual fees, returns, and the counterfactual outcome of an unchosen action.

Open Bandit Dataset is appropriate for off-policy evaluation validation because it is public logged bandit feedback from ZOZOTOWN and includes randomized and bandit-policy logs with propensities. It should validate IPS, self-normalized IPS, direct method, and doubly robust estimators.

Criteo Uplift is appropriate for causal/uplift validation because it comes from randomized incrementality tests and includes treatment assignment plus visit/conversion labels. It remains research-only in this registry.

AuctionNet is a simulation and strategy stress lane. It has large generated auction trajectories, but it is ad-auction behavior, not bilateral eBay retail negotiation.

CraigslistBargain is useful for extracting prices, concessions, and dialogue acts from text. Its participants were crowdworkers, so it cannot calibrate real eBay acceptance probabilities.

Current eBay seller data is the only registered production-export source class. It requires explicit authorization, official API or seller-export boundaries, and seller-provided economics.

## Decision Gate

Run Wave 1, Wave 2, and Wave 3A/3B before flexible model training. Stop if:

- NBER ingestion fails leakage or data-quality audit.
- Strong simple baselines do not beat naive base rates on chronological and seller-disjoint splits.
- Open Bandit off-policy estimates fail support or effective-sample-size checks.
- Criteo negative controls invent uplift.
- Artifact lineage allows restricted research datasets to export production models.

## Sources Checked

- NBER Best Offer Sequential Bargaining: https://www.nber.org/research/data/best-offer-sequential-bargaining
- Open Bandit Pipeline documentation: https://zr-obp.readthedocs.io/en/latest/about.html
- Criteo Uplift Prediction Dataset: https://ailab.criteo.com/criteo-uplift-prediction-dataset/
- AuctionNet paper and repository: https://proceedings.neurips.cc/paper_files/paper/2024/hash/ab9b7c23edfea0011507f7e1eae82cd2-Abstract-Datasets_and_Benchmarks_Track.html and https://github.com/alimama-tech/AuctionNet
- CraigslistBargain: https://huggingface.co/datasets/stanfordnlp/craigslist_bargains
- eBay Seller Hub reports: https://www.ebay.com/help/selling/selling-tools/seller-hub-reports?id=4096
- eBay GetBestOffers: https://developer.ebay.com/devzone/xml/docs/reference/ebay/GetBestOffers.html
- eBay Analytics API: https://developer.ebay.com/api-docs/sell/analytics/overview.html
