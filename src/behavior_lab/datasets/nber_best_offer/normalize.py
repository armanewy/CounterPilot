from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path
from typing import Iterator

from behavior_lab.datasets.nber_best_offer.schema import LISTING_COLUMNS, TRANSFORMATION_VERSION, TURN_COLUMNS


class NberNormalizeError(ValueError):
    pass


def build_sample_dataset(output_dir: str | Path) -> dict[str, object]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    listings = root / "listings.csv"
    turns = root / "turns.csv"
    listings.write_text(
        "\n".join(
            [
                ",".join(LISTING_COLUMNS),
                "l1,s1,refurbished electronics,used,100.0,95.0,2012-05-01T10:00:00,2012-05-05T10:00:00",
                "l2,s2,cameras,used,200.0,190.0,2012-05-02T10:00:00,2012-05-06T10:00:00",
                "l3,s3,refurbished electronics,used,150.0,140.0,2012-05-03T10:00:00,2012-05-07T10:00:00",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    turns.write_text(
        "\n".join(
            [
                ",".join(TURN_COLUMNS),
                "t1,l1,b1,s1,1,buyer,offer,70.0,submitted,2012-05-01T11:00:00",
                "t1,l1,b1,s1,2,seller,counter,85.0,countered,2012-05-01T12:00:00",
                "t1,l1,b1,s1,3,buyer,accept,85.0,accepted,2012-05-01T13:00:00",
                "t2,l2,b2,s2,1,buyer,offer,120.0,submitted,2012-05-02T11:00:00",
                "t2,l2,b2,s2,2,seller,decline,,declined,2012-05-02T12:00:00",
                "t3,l3,b3,s3,1,buyer,offer,110.0,submitted,2012-05-03T11:00:00",
                "t3,l3,b3,s3,2,seller,counter,130.0,countered,2012-05-03T12:00:00",
                "t3,l3,b3,s3,3,buyer,counter,120.0,countered,2012-05-03T13:00:00",
                "t3,l3,b3,s3,4,seller,accept,120.0,accepted,2012-05-03T14:00:00",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {"output_dir": str(root.resolve()), "listings": str(listings), "turns": str(turns), "rows": {"listings": 3, "turns": 9}}


def normalize_dataset(input_dir: str | Path, output_dir: str | Path) -> dict[str, object]:
    source = Path(input_dir)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    quarantine = destination / "quarantine.jsonl"
    listing_count = _normalize_csv(
        _find_file(source, "listings"),
        destination / "listings.jsonl",
        LISTING_COLUMNS,
        _normalize_listing,
        quarantine,
    )
    turn_count = _normalize_csv(
        _find_file(source, "turns"),
        destination / "negotiation_turns.jsonl",
        TURN_COLUMNS,
        _normalize_turn,
        quarantine,
    )
    manifest = {
        "transformation_version": TRANSFORMATION_VERSION,
        "format": "jsonl_partitioned",
        "tables": {
            "listings": {"path": str((destination / "listings.jsonl").resolve()), "rows": listing_count},
            "negotiation_turns": {"path": str((destination / "negotiation_turns.jsonl").resolve()), "rows": turn_count},
        },
        "quarantine": str(quarantine.resolve()),
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _normalize_csv(path: Path, output: Path, required: list[str], normalizer: object, quarantine: Path) -> int:
    count = 0
    with _open_text(path) as handle, output.open("w", encoding="utf-8") as out, quarantine.open("a", encoding="utf-8") as bad:
        reader = csv.DictReader(handle)
        missing = sorted(set(required) - set(reader.fieldnames or []))
        if missing:
            raise NberNormalizeError(f"{path} missing columns {missing}")
        for line_number, row in enumerate(reader, start=2):
            try:
                normalized = normalizer(row)  # type: ignore[misc]
            except Exception as exc:
                bad.write(json.dumps({"path": str(path), "line": line_number, "error": str(exc), "row": row}, sort_keys=True) + "\n")
                continue
            out.write(json.dumps(normalized, sort_keys=True) + "\n")
            count += 1
    return count


def _normalize_listing(row: dict[str, str]) -> dict[str, object]:
    return {
        "source_row_id": row["listing_id"],
        "listing_id": row["listing_id"],
        "seller_id": row["seller_id"],
        "category": row["category"],
        "condition": row["condition"],
        "listing_price": float(row["listing_price"]),
        "reference_price": float(row["reference_price"]) if row["reference_price"] else None,
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "transformation_version": TRANSFORMATION_VERSION,
    }


def _normalize_turn(row: dict[str, str]) -> dict[str, object]:
    return {
        "source_row_id": f"{row['thread_id']}:{row['turn_index']}",
        "thread_id": row["thread_id"],
        "listing_id": row["listing_id"],
        "buyer_id": row["buyer_id"],
        "seller_id": row["seller_id"],
        "turn_index": int(row["turn_index"]),
        "actor": row["actor"],
        "action": row["action"],
        "amount": float(row["amount"]) if row["amount"] else None,
        "status": row["status"],
        "event_time": row["event_time"],
        "transformation_version": TRANSFORMATION_VERSION,
    }


def _find_file(root: Path, stem: str) -> Path:
    for suffix in [".csv", ".csv.gz"]:
        candidate = root / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    raise NberNormalizeError(f"Could not find {stem}.csv or {stem}.csv.gz in {root}")


def _open_text(path: Path) -> Iterator[str]:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def read_jsonl(path: str | Path) -> list[dict[str, object]]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows
