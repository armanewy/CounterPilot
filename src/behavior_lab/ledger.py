from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from behavior_lab.core import new_id, stable_hash, to_jsonable, utc_now


class LedgerIntegrityError(RuntimeError):
    pass


class ImmutableLedger:
    """Append-only JSONL ledger with hash chaining.

    The ledger never mutates prior records. Corrections, retirements, and promotions
    are represented as additional records.
    """

    genesis_hash = "GENESIS"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def append(self, record_type: str, payload: Any, record_id: str | None = None) -> dict[str, Any]:
        previous = self.last_hash()
        body = {
            "record_id": record_id or new_id("r"),
            "record_type": record_type,
            "written_at": utc_now(),
            "previous_hash": previous,
            "payload": to_jsonable(payload),
        }
        body["record_hash"] = stable_hash(body)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(body, sort_keys=True, ensure_ascii=True) + "\n")
        return body

    def scan(self, record_type: str | None = None) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if not self.path.exists():
            return records
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    record = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise LedgerIntegrityError(f"Invalid ledger JSON at line {line_number}") from exc
                if record_type is None or record.get("record_type") == record_type:
                    records.append(record)
        return records

    def payloads(self, record_type: str | None = None) -> list[dict[str, Any]]:
        return [record["payload"] for record in self.scan(record_type)]

    def iter_payloads(self, record_type: str | None = None) -> Iterable[dict[str, Any]]:
        for record in self.scan(record_type):
            yield record["payload"]

    def last_hash(self) -> str:
        last = self.genesis_hash
        for record in self.scan():
            last = str(record["record_hash"])
        return last

    def verify_hash_chain(self) -> bool:
        previous = self.genesis_hash
        for record in self.scan():
            observed_hash = record.get("record_hash")
            if record.get("previous_hash") != previous:
                raise LedgerIntegrityError(
                    f"Broken hash chain at {record.get('record_id')}: "
                    f"expected previous {previous}, found {record.get('previous_hash')}"
                )
            body = dict(record)
            body.pop("record_hash", None)
            expected_hash = stable_hash(body)
            if observed_hash != expected_hash:
                raise LedgerIntegrityError(
                    f"Record hash mismatch at {record.get('record_id')}: "
                    f"expected {expected_hash}, found {observed_hash}"
                )
            previous = str(observed_hash)
        return True

    def latest_by_payload_key(self, record_type: str, key: str, value: str) -> dict[str, Any] | None:
        match = None
        for record in self.scan(record_type):
            payload = record["payload"]
            if payload.get(key) == value:
                match = payload
        return match
