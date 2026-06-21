from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from behavior_lab.data_sources.download import download_to_cache
from behavior_lab.data_sources.registry import default_registry
from behavior_lab.datasets.nber_best_offer import NBER_SOURCE_ID


NBER_DATASET_PAGE = "https://www.nber.org/research/data/best-offer-sequential-bargaining"


@dataclass(frozen=True)
class NberFetchResult:
    source_id: str
    dataset_page: str
    fetched: bool
    explicit_full_download: bool
    cache_entry: dict[str, object] | None
    note: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def fetch_codebook(*, cache_dir: str | Path) -> NberFetchResult:
    registry = default_registry()
    registry.check(NBER_SOURCE_ID, "internal_benchmarking")
    return NberFetchResult(
        source_id=NBER_SOURCE_ID,
        dataset_page=NBER_DATASET_PAGE,
        fetched=False,
        explicit_full_download=False,
        cache_entry=None,
        note="Codebook discovery is recorded; use the NBER page to confirm file URLs before full download.",
    )


def fetch_full(*, cache_dir: str | Path, url: str | None = None, explicit: bool = False) -> NberFetchResult:
    if not explicit:
        raise ValueError("Full NBER download requires explicit=True")
    if not url:
        raise ValueError("Full NBER download requires an explicit file URL from the official NBER page")
    cached = download_to_cache(url, cache_dir=cache_dir)
    return NberFetchResult(
        source_id=NBER_SOURCE_ID,
        dataset_page=NBER_DATASET_PAGE,
        fetched=True,
        explicit_full_download=True,
        cache_entry=cached.to_dict(),
        note="Downloaded from caller-provided official source URL; verify NBER terms before commercial use.",
    )
