from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

from behavior_lab.core import stable_hash, utc_now
from behavior_lab.ledger import ImmutableLedger
from behavior_lab.models import SOFTWARE_VERSION
from behavior_lab.stress import LabStressTester


@dataclass(frozen=True)
class BatchConfig:
    worlds: list[str]
    seeds: list[int]
    episode_counts: list[int]

    def __post_init__(self) -> None:
        if not self.worlds or not self.seeds or not self.episode_counts:
            raise ValueError("worlds, seeds, and episode_counts may not be empty")
        if any(count <= 0 for count in self.episode_counts):
            raise ValueError("episode counts must be positive")

    def hash(self) -> str:
        return stable_hash(asdict(self))


class RunAlreadyLocked(RuntimeError):
    pass


class RunDataMismatch(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class RunLock:
    def __init__(
        self,
        path: str | Path,
        payload: dict[str, Any] | None = None,
        *,
        stale_after_seconds: float = 6 * 60 * 60,
    ):
        self.path = Path(path)
        self.payload = payload or {}
        self.stale_after_seconds = stale_after_seconds
        self._fd: int | None = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            if self._stale():
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                return self.__enter__()
            raise RunAlreadyLocked(f"Run lock already exists: {self.path}") from exc
        body = dict(self.payload, locked_at=utc_now(), created_epoch=time.time(), pid=os.getpid())
        os.write(self._fd, json.dumps(body, sort_keys=True).encode("utf-8"))
        os.fsync(self._fd)
        return self

    def _stale(self) -> bool:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return False
        except OSError:
            return False
        age = time.time() - stat.st_mtime
        if age < self.stale_after_seconds:
            return False
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(payload.get("pid", -1))
        except (OSError, ValueError, json.JSONDecodeError):
            return True
        return not _pid_alive(pid)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class SyntheticBatchRunner:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)

    def run(self, config: BatchConfig) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        batch_hash = config.hash()
        source_tree_hash = _source_tree_hash()
        code_commit = _git_commit()
        for world in config.worlds:
            for seed in config.seeds:
                for episodes in config.episode_counts:
                    run_spec = {
                        "world": world,
                        "seed": seed,
                        "episodes": episodes,
                        "software_version": SOFTWARE_VERSION,
                        "source_tree_hash": source_tree_hash,
                    }
                    run_spec_hash = stable_hash(run_spec)
                    run_id = f"{world}-seed{seed}-n{episodes}-{source_tree_hash[:8]}"
                    run_dir = self.base_dir / run_id
                    ledger = ImmutableLedger(run_dir / "ledger.jsonl")
                    if self._completed(ledger, run_id, run_spec_hash):
                        reports.append({"run_id": run_id, "status": "skipped", "reason": "already_complete"})
                        continue
                    with RunLock(
                        run_dir / ".run.lock",
                        {"run_id": run_id, "run_spec_hash": run_spec_hash, "batch_hash": batch_hash},
                    ):
                        # Another process may have completed while this process was
                        # waiting for the lock.
                        if self._completed(ledger, run_id, run_spec_hash):
                            reports.append({"run_id": run_id, "status": "skipped", "reason": "already_complete"})
                            continue
                        self._validate_prior_run_spec(ledger, run_id, run_spec_hash)
                        ledger.append(
                            "research_run_start",
                            {
                                "run_id": run_id,
                                **run_spec,
                                "run_spec_hash": run_spec_hash,
                                "batch_hash": batch_hash,
                                "code_commit": code_commit,
                                "software_version": SOFTWARE_VERSION,
                                "source_tree_hash": source_tree_hash,
                                "started_at": utc_now(),
                            },
                        )
                        try:
                            report = LabStressTester().run(
                                run_dir,
                                episodes=episodes,
                                seed=seed,
                                world=world,
                                require_exact_dataset=True,
                            )
                            ledger.append(
                                "research_run_end",
                                {
                                    "run_id": run_id,
                                    "status": "complete",
                                    "run_spec_hash": run_spec_hash,
                                    "batch_hash": batch_hash,
                                    "ended_at": utc_now(),
                                    "summary": report,
                                },
                            )
                            reports.append({"run_id": run_id, "status": "complete", "summary": report})
                        except Exception as exc:
                            ledger.append(
                                "research_run_end",
                                {
                                    "run_id": run_id,
                                    "status": "failed",
                                    "run_spec_hash": run_spec_hash,
                                    "batch_hash": batch_hash,
                                    "ended_at": utc_now(),
                                    "error": repr(exc),
                                },
                            )
                            raise
        return reports

    def _completed(self, ledger: ImmutableLedger, run_id: str, run_spec_hash: str) -> bool:
        return any(
            payload.get("run_id") == run_id
            and payload.get("run_spec_hash") == run_spec_hash
            and payload.get("status") == "complete"
            for payload in ledger.payloads("research_run_end")
        )

    def _validate_prior_run_spec(self, ledger: ImmutableLedger, run_id: str, run_spec_hash: str) -> None:
        prior = [
            payload
            for payload in ledger.payloads("research_run_start")
            if payload.get("run_id") == run_id
        ]
        mismatches = [payload for payload in prior if payload.get("run_spec_hash") not in {None, run_spec_hash}]
        if mismatches:
            raise RunDataMismatch(
                f"Run directory {run_id!r} contains a different run specification; use a clean directory"
            )


def _source_tree_hash() -> str:
    package_root = Path(__file__).resolve().parent
    files = []
    for path in sorted(package_root.glob("*.py")):
        files.append({"path": path.name, "content": path.read_text(encoding="utf-8")})
    return stable_hash(files)


def _git_commit() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return "unknown"
    return result.stdout.strip()
