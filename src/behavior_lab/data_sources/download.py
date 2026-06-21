from __future__ import annotations

from pathlib import Path
from urllib.request import Request, urlopen

from behavior_lab.data_sources.cache import CachedFile, ContentAddressedCache


class DownloadError(RuntimeError):
    pass


def download_to_cache(url: str, *, cache_dir: str | Path, original_name: str | None = None, timeout: int = 60) -> CachedFile:
    request = Request(url, headers={"User-Agent": "BehaviorDiscoveryLab/0.3"})
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310: explicit user-requested dataset download helper
            return ContentAddressedCache(cache_dir).add_stream(response, original_name or Path(url).name or "download.bin")
    except Exception as exc:  # pragma: no cover - network exercised manually
        raise DownloadError(f"Failed to download {url!r}: {exc}") from exc
