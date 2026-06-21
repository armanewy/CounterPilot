# NBER Source Inventory

Status: source-inventory contract. Raw source files belong under `OFFERLAB_DATA_ROOT/raw/nber_best_offer` and must not be committed.

## Official Files

| File | URL | Observed metadata |
| --- | --- | --- |
| `anon_bo_lists.csv.gz` | `https://www.nber.org/bargaining/anon_bo_lists.csv.gz` | ETag prefix `10956f7aa`, consistent with about 4.45 GB compressed |
| `anon_bo_threads.csv.gz` | `https://www.nber.org/bargaining/anon_bo_threads.csv.gz` | ETag prefix `51e6bd20`, consistent with about 1.37 GB compressed |
| `Codebook.xlsx` | `https://www.nber.org/bargaining/Codebook.xlsx` | SHA-256 `3FA5E83046AC29E610CF2BCF02FD85682F93F3608C689C5A434D794C65BB6516` |

The repository includes `src/behavior_lab/datasets/nber_best_offer/source_inventory.py` and the CLI command:

```powershell
$env:OFFERLAB_DATA_ROOT = "C:\OfferLabData"
python -m behavior_lab nber-best-offer source-inventory --download
```

The command uses atomic temporary files, byte-range resume where supported, SHA-256 hashes, gzip integrity checks, streaming row counts, redacted row summaries, and deterministic redacted samples outside the repository.

## Privacy And Release Boundaries

The command hashes anonymized item, buyer, seller, product, title, and thread identifiers in printed summaries. It does not write raw samples into the repository. NBER-derived artifacts remain research-only and non-exportable.

## Current Local Evidence

On 2026-06-21, only source docs and ranged header probes were used in the repository work. The full raw CSV downloads are intentionally not committed. A full inventory report should be generated locally after `--download`; the generated report may record metadata and hashes, but not raw records.
