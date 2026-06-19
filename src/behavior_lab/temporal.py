from __future__ import annotations

from typing import Any

from behavior_lab.core import parse_time


POST_DECISION_KEYS = {"observed_action", "later_outcomes", "outcomes", "adherence"}


class TemporalLeakageError(RuntimeError):
    pass


def pre_decision_snapshot(episode: dict[str, Any]) -> dict[str, Any]:
    """Return only fields that were knowable before the decision outcome."""

    return {
        "episode_id": episode["episode_id"],
        "subject_id": episode["subject_id"],
        "decision_time": episode["decision_time"],
        "observation_cutoff": episode["observation_cutoff"],
        "situation": dict(episode.get("situation", {})),
        "available_actions": list(episode.get("available_actions", [])),
        "pre_decision_context": dict(episode.get("pre_decision_context", {})),
        "data_provenance": dict(episode.get("data_provenance", {})),
    }


def assert_snapshot_is_pre_decision(snapshot: dict[str, Any]) -> None:
    leaked = POST_DECISION_KEYS.intersection(snapshot.keys())
    if leaked:
        raise TemporalLeakageError(f"Snapshot contains post-decision keys: {sorted(leaked)}")


def supervised_row(episode: dict[str, Any], target_name: str) -> dict[str, Any] | None:
    outcomes = episode.get("later_outcomes") or {}
    if target_name not in outcomes:
        return None
    snapshot = pre_decision_snapshot(episode)
    assert_snapshot_is_pre_decision(snapshot)
    features = dict(snapshot["pre_decision_context"])
    features["bias"] = 1.0
    return {
        "case_id": episode["episode_id"],
        "decision_time": episode["decision_time"],
        "features": features,
        "target": 1 if bool(outcomes[target_name]) else 0,
        "snapshot": snapshot,
    }


def supervised_rows(
    episodes: list[dict[str, Any]],
    target_name: str,
    *,
    through_time: str | None = None,
) -> list[dict[str, Any]]:
    cutoff = parse_time(through_time) if through_time else None
    rows: list[dict[str, Any]] = []
    for episode in episodes:
        if cutoff and parse_time(episode["decision_time"]) > cutoff:
            continue
        row = supervised_row(episode, target_name)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda item: item["decision_time"])
    return rows


def split_rows(
    rows: list[dict[str, Any]],
    train_fraction: float = 0.5,
    development_fraction: float = 0.2,
    hidden_fraction: float = 0.2,
) -> dict[str, list[dict[str, Any]]]:
    """Temporal split: training, development, hidden, prospective."""

    n = len(rows)
    train_end = max(1, int(n * train_fraction))
    dev_end = max(train_end + 1, int(n * (train_fraction + development_fraction)))
    hidden_end = max(dev_end + 1, int(n * (train_fraction + development_fraction + hidden_fraction)))
    return {
        "training": rows[:train_end],
        "development": rows[train_end:dev_end],
        "hidden": rows[dev_end:hidden_end],
        "prospective": rows[hidden_end:],
    }


def feature_catalog(rows: list[dict[str, Any]]) -> list[str]:
    names: set[str] = set()
    for row in rows:
        for name, value in row["features"].items():
            if name == "bias":
                continue
            if isinstance(value, (int, float, bool)):
                names.add(name)
    return sorted(names)
