# OfferLab Transfer Results

Wave 4 treats external benchmark sources as ancillary validation, not as pooled
training rows for eBay offer prediction.

Current default ablation result: **do not retain transfer features**.

Reason:

- The NBER Best Offer benchmark is the direct behavioral evidence source.
- Open Bandit and Criteo validate evaluator and causal machinery, but their rows
  are not eBay bargaining rows.
- CraigslistBargain is useful for dialogue-act extraction only.
- Transfer is retained only when it improves NBER hidden loss and calibration
  simultaneously.

Policy:

- Raw-row pooling across domains is disabled.
- Transfer artifacts are research-only.
- Production export remains blocked unless a future artifact is trained on
  authorized seller data with confirmed commercial-use rights.
