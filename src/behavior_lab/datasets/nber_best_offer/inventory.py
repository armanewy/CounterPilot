from __future__ import annotations

import csv
import gzip
from pathlib import Path
from typing import Iterator


def inventory_path(path: str | Path) -> dict[str, object]:
    source = Path(path)
    return {
        "path": str(source.resolve()),
        "exists": source.exists(),
        "bytes": source.stat().st_size if source.exists() else None,
        "rows": count_rows(source) if source.exists() and source.is_file() else None,
        "compressed": source.suffix == ".gz",
    }


def count_rows(path: str | Path) -> int:
    with _open_text(Path(path)) as handle:
        reader = csv.reader(handle)
        count = -1
        for count, _row in enumerate(reader):
            pass
    return max(count, 0)


def _open_text(path: Path) -> Iterator[str]:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")
