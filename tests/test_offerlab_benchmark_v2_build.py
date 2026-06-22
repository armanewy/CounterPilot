from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import _bootstrap  # noqa: F401,E402

from behavior_lab.datasets.nber_best_offer.real_normalize import sha256_file
from behavior_lab.offerlab_models.common import _hidden_case_tokens
from behavior_lab.offerlab_models.benchmark_v2 import (
    BenchmarkV2Paths,
    build_offerlab_benchmark_v2,
    read_v2_task_rows,
)


class OfferLabBenchmarkV2BuildTests(unittest.TestCase):
    def test_builds_all_targets_and_preserves_unknown_censored_without_label_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            normalized = _write_normalized(root / "normalized")
            tokens = root / "v1_tokens.json"
            tokens.write_text(json.dumps({"tokens": ["spent-v1"]}), encoding="utf-8")

            report = build_offerlab_benchmark_v2(
                BenchmarkV2Paths(
                    normalized_dir=normalized,
                    output_dir=root / "v2",
                    external_v1_hidden_tokens_path=tokens,
                ),
                require_full_release=False,
                partition_rows=3,
            )

            self.assertEqual(set(report["task_manifests"]), {
                "seller_next_action",
                "buyer_response_to_counter",
                "agreement",
                "final_price_ratio",
                "response_latency",
            })
            for target, counts in report["task_manifests"].items():
                self.assertGreater(counts["eligible_rows"], 0, target)
                self.assertFalse(counts["unknown_and_censored_labeled_as_rejection"], target)
            self.assertGreater(report["task_manifests"]["seller_next_action"]["censored_outcome_rows"], 0)
            self.assertGreater(report["task_manifests"]["final_price_ratio"]["unknown_outcome_rows"], 0)

            split = json.loads((root / "v2" / "splits" / "chronological_listing_purged" / "seller_next_action.json").read_text(encoding="utf-8"))
            hidden_rows = list(
                read_v2_task_rows(
                    root / "v2",
                    "seller_next_action",
                    split_manifest="chronological_listing_purged",
                    split_region="hidden",
                )
            )
            self.assertTrue(hidden_rows)
            self.assertTrue(all("label" not in row for row in hidden_rows))
            self.assertTrue(all(row.get("label_redacted") is True for row in hidden_rows))
            self.assertTrue((root / "v2" / "protected_labels" / "seller_next_action.jsonl").exists())
            self.assertTrue(split["validation"]["passed"])
            self.assertGreaterEqual(split["purged_rows"], 1)

    def test_split_manifests_block_listing_seller_buyer_thread_and_category_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            normalized = _write_normalized(root / "normalized")
            tokens = root / "v1_tokens.json"
            tokens.write_text(json.dumps({"tokens": ["spent-v1"]}), encoding="utf-8")
            build_offerlab_benchmark_v2(
                BenchmarkV2Paths(
                    normalized_dir=normalized,
                    output_dir=root / "v2",
                    external_v1_hidden_tokens_path=tokens,
                ),
                require_full_release=False,
                partition_rows=4,
            )

            public_rows = _read_public_rows(root / "v2", "seller_next_action")
            self.assertTrue(public_rows)
            self.assertTrue(all(set(row["features"]).isdisjoint({"listing_id", "seller_id", "buyer_id", "thread_id", "status_id", "response_time"}) for row in public_rows.values()))

            split_expectations = {
                "chronological_listing_purged": "listing_id",
                "seller_disjoint": "seller_id",
                "buyer_disjoint": "buyer_id",
                "category_disjoint_diagnostic": "category",
                "thread_safe_nested_development": "thread_id",
            }
            for split_name, group_key in split_expectations.items():
                with self.subTest(split=split_name):
                    manifest = json.loads((root / "v2" / "splits" / split_name / "seller_next_action.json").read_text(encoding="utf-8"))
                    self.assertTrue(manifest["validation"]["passed"])
                    self.assertTrue(_groups_are_disjoint(manifest["assignments"], public_rows, group_key))

            buyer_manifest = json.loads((root / "v2" / "splits" / "buyer_disjoint" / "seller_next_action.json").read_text(encoding="utf-8"))
            self.assertGreater(buyer_manifest["missing_identifier_rows"], 0)
            self.assertEqual(buyer_manifest["purged_rows"], buyer_manifest["missing_identifier_rows"])

    def test_timestamp_ties_are_deterministic_and_identifier_aliases_canonicalize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            normalized = _write_normalized(root / "normalized")
            tokens = root / "v1_tokens.json"
            tokens.write_text(json.dumps({"tokens": ["spent-v1"]}), encoding="utf-8")
            first = build_offerlab_benchmark_v2(
                BenchmarkV2Paths(
                    normalized_dir=normalized,
                    output_dir=root / "v2a",
                    external_v1_hidden_tokens_path=tokens,
                ),
                require_full_release=False,
            )
            second = build_offerlab_benchmark_v2(
                BenchmarkV2Paths(
                    normalized_dir=normalized,
                    output_dir=root / "v2b",
                    external_v1_hidden_tokens_path=tokens,
                ),
                require_full_release=False,
            )

            first_split = json.loads((root / "v2a" / "splits" / "seller_disjoint" / "seller_next_action.json").read_text(encoding="utf-8"))
            second_split = json.loads((root / "v2b" / "splits" / "seller_disjoint" / "seller_next_action.json").read_text(encoding="utf-8"))
            self.assertEqual(first_split["case_set_hash"], second_split["case_set_hash"])
            self.assertEqual(first["task_manifests"], second["task_manifests"])

            rows = _read_public_rows(root / "v2a", "seller_next_action")
            alias_rows = [row for row in rows.values() if row["listing_id"] == "listing-alias"]
            self.assertTrue(alias_rows)
            self.assertTrue(all(row["seller_id"] == "seller-alias" for row in alias_rows))

    def test_v1_hidden_overlap_is_excluded_from_fresh_lockbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            normalized = _write_normalized(root / "normalized")
            tokens = root / "v1_tokens.json"
            tokens.write_text(json.dumps({"tokens": ["spent-v1"]}), encoding="utf-8")
            build_offerlab_benchmark_v2(
                BenchmarkV2Paths(
                    normalized_dir=normalized,
                    output_dir=root / "first",
                    external_v1_hidden_tokens_path=tokens,
                ),
                require_full_release=False,
            )
            lockbox = json.loads((root / "first" / "fresh_hidden_lockbox" / "seller_next_action.json").read_text(encoding="utf-8"))
            overlap_row = lockbox["assignments"]["hidden"][0]
            conn = sqlite3.connect(root / "first" / "benchmark_v2.sqlite")
            try:
                overlap_token = conn.execute("SELECT case_token FROM cases WHERE row_id = ?", (overlap_row,)).fetchone()[0]
            finally:
                conn.close()
            tokens.write_text(json.dumps({"tokens": ["spent-v1", overlap_token]}), encoding="utf-8")

            build_offerlab_benchmark_v2(
                BenchmarkV2Paths(
                    normalized_dir=normalized,
                    output_dir=root / "second",
                    external_v1_hidden_tokens_path=tokens,
                ),
                require_full_release=False,
            )
            second_lockbox = json.loads((root / "second" / "fresh_hidden_lockbox" / "seller_next_action.json").read_text(encoding="utf-8"))
            self.assertEqual(second_lockbox["excluded_overlap_rows"], 1)
            self.assertNotIn(overlap_row, second_lockbox["assignments"]["hidden"])

    def test_v1_shaped_hidden_token_overlap_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            normalized = _write_normalized(root / "normalized")
            tokens = root / "v1_tokens.json"
            tokens.write_text(json.dumps({"tokens": ["spent-v1"]}), encoding="utf-8")
            build_offerlab_benchmark_v2(
                BenchmarkV2Paths(
                    normalized_dir=normalized,
                    output_dir=root / "first",
                    external_v1_hidden_tokens_path=tokens,
                ),
                require_full_release=False,
            )
            lockbox = json.loads((root / "first" / "fresh_hidden_lockbox" / "seller_next_action.json").read_text(encoding="utf-8"))
            overlap_row = lockbox["assignments"]["hidden"][0]
            public_rows = _read_public_rows(root / "first", "seller_next_action")
            v1_compatible_token = _hidden_case_tokens([public_rows[overlap_row]])[0]
            tokens.write_text(json.dumps({"tokens": [v1_compatible_token]}), encoding="utf-8")

            build_offerlab_benchmark_v2(
                BenchmarkV2Paths(
                    normalized_dir=normalized,
                    output_dir=root / "second",
                    external_v1_hidden_tokens_path=tokens,
                ),
                require_full_release=False,
            )

            second_lockbox = json.loads((root / "second" / "fresh_hidden_lockbox" / "seller_next_action.json").read_text(encoding="utf-8"))
            self.assertEqual(second_lockbox["excluded_overlap_rows"], 1)
            self.assertNotIn(overlap_row, second_lockbox["assignments"]["hidden"])

    def test_fresh_hidden_lockbox_refuses_empty_candidates_after_v1_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            normalized = _write_normalized(root / "normalized")
            tokens = root / "v1_tokens.json"
            tokens.write_text(json.dumps({"tokens": ["spent-v1"]}), encoding="utf-8")
            build_offerlab_benchmark_v2(
                BenchmarkV2Paths(
                    normalized_dir=normalized,
                    output_dir=root / "first",
                    external_v1_hidden_tokens_path=tokens,
                ),
                require_full_release=False,
            )
            lockbox = json.loads((root / "first" / "fresh_hidden_lockbox" / "seller_next_action.json").read_text(encoding="utf-8"))
            public_rows = _read_public_rows(root / "first", "seller_next_action")
            all_hidden_tokens = sorted(
                {
                    token
                    for row_id in lockbox["assignments"]["hidden"]
                    for token in _hidden_case_tokens([public_rows[row_id]])
                }
            )
            tokens.write_text(json.dumps({"tokens": all_hidden_tokens}), encoding="utf-8")

            with self.assertRaisesRegex(Exception, "no candidates"):
                build_offerlab_benchmark_v2(
                    BenchmarkV2Paths(
                        normalized_dir=normalized,
                        output_dir=root / "second",
                        external_v1_hidden_tokens_path=tokens,
                    ),
                    require_full_release=False,
                )


def _write_normalized(root: Path) -> Path:
    listings = [
        _listing("listing-shared", "seller-shared", "cat-a", sold=True, price=100, final=80),
        _listing("listing-2", "seller-shared", "cat-b", sold=True, price=100, final=81),
        _listing("listing-3", "seller-3", "cat-c", sold=False, price=120, final=None),
        _listing("listing-4", "seller-4", "cat-d", sold=True, price=90, final=75),
        _listing("listing-5", "seller-5", "cat-e", sold=True, price=140, final=100),
        _listing("listing-6", "seller-6", "cat-f", sold=False, price=160, final=None),
        _listing("listing-7", "seller-7", "cat-g", sold=True, price=130, final=99),
        _listing("listing-8", "seller-8", "cat-h", sold=True, price=110, final=91),
        _listing("listing-9", "seller-9", "cat-i", sold=False, price=115, final=None),
        _listing("listing-10", "seller-10", "cat-j", sold=True, price=125, final=104),
        _listing("listing-11", "seller-11", "cat-k", sold=True, price=135, final=111),
        _listing("listing-12", "seller-12", "cat-l", sold=True, price=145, final=120),
        _listing("listing-alias", " seller-alias ", "cat-m", sold=True, price=150, final=121),
    ]
    turns = []
    for index in range(1, 13):
        listing_id = "listing-shared" if index in {1, 7, 12} else f"listing-{index}"
        seller_id = "seller-shared" if index in {1, 2, 7, 12} else f"seller-{index}"
        buyer_id = "" if index == 5 else f"buyer-{index}"
        status_id = 8 if index == 6 else (7 if index in {3, 9} else (2 if index in {4, 10} else 1))
        event_time = "2020-01-05T00:00:00" if index in {5, 6} else f"2020-01-{index:02d}T00:00:00"
        turns.append(_turn(f"thread-{index}", listing_id, buyer_id, seller_id, 1, "buyer", "offer", status_id, 70 + index, event_time, "2020-02-01T00:00:00"))
        if status_id == 7:
            turns.append(_turn(f"thread-{index}", listing_id, buyer_id, seller_id, 2, "seller", "counter", 1, 80 + index, f"2020-01-{index:02d}T01:00:00", "2020-02-01T00:00:00"))
    turns.append(_turn("thread-alias", " LISTING-ALIAS ", "buyer-alias", "SELLER-ALIAS", 1, "buyer", "offer", 1, 90, "2020-01-13T00:00:00", "2020-02-01T00:00:00"))

    tables = root / "tables"
    listing_dir = tables / "listings"
    turn_dir = tables / "negotiation_turns"
    listing_dir.mkdir(parents=True)
    turn_dir.mkdir(parents=True)
    listing_parts = _write_parts(listing_dir, "listings", listings, rows_per_part=5)
    turn_parts = _write_parts(turn_dir, "turns", sorted(turns, key=lambda row: (row["thread_id"], row["turn_index"])), rows_per_part=4)
    manifest = {
        "status": "complete",
        "schema_version": "test_normalized_manifest.v1",
        "source_dataset_ids": ["nber_ebay_best_offer"],
        "research_only": True,
        "production_export_allowed": False,
        "command_args": {"full": True, "limit_threads": None},
        "lineage": {"normalization_manifest_hash": "test-manifest"},
        "tables": {
            "listings": {"path": str(listing_dir), "format": "jsonl", "rows": len(listings), "partitions": listing_parts},
            "negotiation_turns": {"path": str(turn_dir), "format": "jsonl", "rows": len(turns), "partitions": turn_parts},
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return root


def _listing(listing_id: str, seller_id: str, category: str, *, sold: bool, price: float, final: float | None) -> dict:
    return {
        "listing_id": listing_id,
        "seller_id": seller_id,
        "category": category,
        "condition": "used",
        "listing_price": price,
        "final_sale_price": final,
        "sold_by_best_offer": sold,
    }


def _turn(thread_id: str, listing_id: str, buyer_id: str, seller_id: str, turn_index: int, actor: str, action: str, status_id: int, amount: float, event_time: str, response_time: str) -> dict:
    return {
        "thread_id": thread_id,
        "listing_id": listing_id,
        "buyer_id": buyer_id,
        "seller_id": seller_id,
        "turn_index": turn_index,
        "actor": actor,
        "action": action,
        "amount": amount,
        "status": str(status_id),
        "status_id": status_id,
        "event_time": event_time,
        "response_time": response_time,
    }


def _write_parts(directory: Path, prefix: str, rows: list[dict], *, rows_per_part: int) -> list[dict]:
    parts = []
    for index, start in enumerate(range(0, len(rows), rows_per_part)):
        path = directory / f"{prefix}_{index:05d}.jsonl"
        chunk = rows[start : start + rows_per_part]
        path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in chunk), encoding="utf-8")
        parts.append({"path": str(path), "rows": len(chunk), "sha256": sha256_file(path)})
    return parts


def _read_public_rows(root: Path, target: str) -> dict[str, dict]:
    rows = {}
    for row in read_v2_task_rows(root, target):
        rows[row["row_id"]] = row
    return rows


def _groups_are_disjoint(assignments: dict[str, list[str]], rows: dict[str, dict], group_key: str) -> bool:
    seen: dict[str, str] = {}
    for region, row_ids in assignments.items():
        for row_id in row_ids:
            group = rows[row_id].get(group_key)
            if not group:
                continue
            previous = seen.setdefault(group, region)
            if previous != region:
                return False
    return True


if __name__ == "__main__":
    unittest.main()
