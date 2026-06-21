from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import shutil
from typing import BinaryIO


@dataclass(frozen=True)
class CachedFile:
    sha256: str
    bytes: int
    path: str
    original_name: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ContentAddressedCache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)

    def add_file(self, path: str | Path) -> CachedFile:
        source = Path(path)
        digest = _sha256_file(source)
        destination = self.objects / digest[:2] / digest
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            tmp = destination.with_suffix(".tmp")
            shutil.copyfile(source, tmp)
            tmp.replace(destination)
        cached = CachedFile(digest, source.stat().st_size, str(destination), source.name)
        self._write_metadata(cached)
        return cached

    def add_stream(self, stream: BinaryIO, original_name: str) -> CachedFile:
        digest = hashlib.sha256()
        tmp = self.root / f"{original_name}.tmp"
        size = 0
        with tmp.open("wb") as output:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
                output.write(chunk)
        sha = digest.hexdigest()
        destination = self.objects / sha[:2] / sha
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            tmp.unlink()
        else:
            tmp.replace(destination)
        cached = CachedFile(sha, size, str(destination), original_name)
        self._write_metadata(cached)
        return cached

    def inspect(self, sha256: str) -> dict[str, object]:
        path = self.objects / sha256[:2] / sha256
        return {
            "sha256": sha256,
            "exists": path.exists(),
            "path": str(path),
            "bytes": path.stat().st_size if path.exists() else None,
        }

    def _write_metadata(self, cached: CachedFile) -> None:
        metadata = self.root / "manifest.jsonl"
        with metadata.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(cached.to_dict(), sort_keys=True) + "\n")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
