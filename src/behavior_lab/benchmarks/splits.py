from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class SplitAssignment:
    train: list[dict[str, Any]]
    development: list[dict[str, Any]]
    hidden: list[dict[str, Any]]

    def sizes(self) -> dict[str, int]:
        return {"train": len(self.train), "development": len(self.development), "hidden": len(self.hidden)}


def chronological_split(rows: Iterable[dict[str, Any]], *, time_key: str, train_fraction: float = 0.6, development_fraction: float = 0.2) -> SplitAssignment:
    ordered = sorted(rows, key=lambda row: str(row.get(time_key, "")))
    if not ordered:
        return SplitAssignment([], [], [])
    if train_fraction <= 0 or development_fraction < 0 or train_fraction + development_fraction >= 1:
        raise ValueError("fractions must leave a non-empty hidden region")
    train_end = max(1, int(len(ordered) * train_fraction))
    development_end = max(train_end, int(len(ordered) * (train_fraction + development_fraction)))
    if len(ordered) >= 3:
        development_end = min(development_end, len(ordered) - 1)
    return SplitAssignment(ordered[:train_end], ordered[train_end:development_end], ordered[development_end:])


def group_disjoint_split(rows: Iterable[dict[str, Any]], *, group_key: str, train_fraction: float = 0.6, development_fraction: float = 0.2) -> SplitAssignment:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get(group_key, "")), []).append(row)
    ordered_groups = sorted(groups)
    if not ordered_groups:
        return SplitAssignment([], [], [])
    train_end = max(1, int(len(ordered_groups) * train_fraction))
    development_end = max(train_end, int(len(ordered_groups) * (train_fraction + development_fraction)))
    if len(ordered_groups) >= 3:
        development_end = min(development_end, len(ordered_groups) - 1)
    train_groups = set(ordered_groups[:train_end])
    dev_groups = set(ordered_groups[train_end:development_end])
    hidden_groups = set(ordered_groups[development_end:])
    return SplitAssignment(
        [row for row in rows if str(row.get(group_key, "")) in train_groups],
        [row for row in rows if str(row.get(group_key, "")) in dev_groups],
        [row for row in rows if str(row.get(group_key, "")) in hidden_groups],
    )


def assert_disjoint_groups(split: SplitAssignment, *, group_key: str) -> bool:
    sets = []
    for rows in [split.train, split.development, split.hidden]:
        sets.append({str(row.get(group_key, "")) for row in rows})
    return not (sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2])
