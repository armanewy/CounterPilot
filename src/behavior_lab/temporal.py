from __future__ import annotations

from typing import Any

from behavior_lab.core import parse_time


POST_DECISION_KEYS = {
    "observed_action",
    "later_outcomes",
    "outcomes",
    "adherence",
    "target",
    "label",
    "actual_action",
}


class TemporalLeakageError(RuntimeError):
    pass


def _find_reserved_keys(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if key_text.lower() in POST_DECISION_KEYS:
                found.append(child_path)
            found.extend(_find_reserved_keys(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_reserved_keys(item, f"{path}[{index}]"))
    return found


def pre_decision_snapshot(episode: dict[str, Any]) -> dict[str, Any]:
    """Return only fields intended to be knowable before the outcome.

    Data provenance is deliberately excluded. Provenance often contains capture
    metadata or synthetic-world annotations that are useful to the laboratory but
    should never become prediction context.
    """

    snapshot = {
        "episode_id": episode["episode_id"],
        "subject_id": episode["subject_id"],
        "decision_time": episode["decision_time"],
        "observation_cutoff": episode["observation_cutoff"],
        "situation": dict(episode.get("situation", {})),
        "available_actions": list(episode.get("available_actions", [])),
        "pre_decision_context": dict(episode.get("pre_decision_context", {})),
    }
    assert_snapshot_is_pre_decision(snapshot)
    return snapshot


def assert_snapshot_is_pre_decision(snapshot: dict[str, Any]) -> None:
    leaked = _find_reserved_keys(snapshot)
    if leaked:
        raise TemporalLeakageError(f"Snapshot contains post-decision/reserved keys: {sorted(leaked)}")
    decision_time = parse_time(str(snapshot.get("decision_time")))
    cutoff = parse_time(str(snapshot.get("observation_cutoff")))
    if cutoff > decision_time:
        raise TemporalLeakageError("observation_cutoff occurs after decision_time")




def assert_feature_map_is_pre_decision(
    features: dict[str, Any], *, target_name: str | None = None
) -> None:
    leaked = _find_reserved_keys(features)
    if target_name and target_name in features:
        leaked.append(target_name)
    if leaked:
        raise TemporalLeakageError(
            f"Feature map contains post-decision/outcome-like keys: {sorted(set(leaked))}"
        )


def supervised_row(episode: dict[str, Any], target_name: str) -> dict[str, Any] | None:
    outcomes = episode.get("later_outcomes") or {}
    if target_name not in outcomes:
        return None
    snapshot = pre_decision_snapshot(episode)
    features = dict(snapshot["pre_decision_context"])
    assert_feature_map_is_pre_decision(features, target_name=target_name)
    features["bias"] = 1.0
    return {
        "case_id": episode["episode_id"],
        "decision_time": episode["decision_time"],
        "observation_cutoff": episode["observation_cutoff"],
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
        decision_time = parse_time(episode["decision_time"])
        observation_cutoff = parse_time(episode["observation_cutoff"])
        if cutoff and (decision_time > cutoff or observation_cutoff > cutoff):
            continue
        row = supervised_row(episode, target_name)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda item: (parse_time(item["decision_time"]), str(item["case_id"])))
    return rows


def split_rows(
    rows: list[dict[str, Any]],
    train_fraction: float = 0.6,
    development_fraction: float = 0.2,
    hidden_fraction: float = 0.2,
    prospective_fraction: float = 0.0,
) -> dict[str, list[dict[str, Any]]]:
    """Create a chronological manifest.

    The initial manifest intentionally has no prospective cases. Prospective means
    collected after a model freeze, not merely the newest fraction of an old file.
    """

    fractions = [train_fraction, development_fraction, hidden_fraction, prospective_fraction]
    if any(value < 0 for value in fractions):
        raise ValueError("split fractions may not be negative")
    total = sum(fractions)
    if total <= 0 or total > 1.0 + 1e-9:
        raise ValueError("split fractions must sum to a value in (0, 1]")

    ordered = sorted(rows, key=lambda item: (parse_time(item["decision_time"]), str(item["case_id"])))
    n = len(ordered)
    if n == 0:
        return {"training": [], "development": [], "hidden": [], "prospective": []}

    # Allocate by floors, then hand any remainder to hidden (or training for tiny
    # datasets). This keeps prospective empty unless explicitly requested.
    train_n = int(n * train_fraction)
    dev_n = int(n * development_fraction)
    hidden_n = int(n * hidden_fraction)
    prospective_n = int(n * prospective_fraction)

    train_n = max(1, train_n)
    if n >= 2 and development_fraction > 0:
        dev_n = max(1, dev_n)
    if n >= 3 and hidden_fraction > 0:
        hidden_n = max(1, hidden_n)

    allocated = train_n + dev_n + hidden_n + prospective_n
    if allocated > n:
        overflow = allocated - n
        for name in ["prospective", "hidden", "development"]:
            if overflow <= 0:
                break
            current = {"development": dev_n, "hidden": hidden_n, "prospective": prospective_n}[name]
            minimum = 1 if (name == "development" and n >= 2) or (name == "hidden" and n >= 3) else 0
            removable = max(0, current - minimum)
            take = min(removable, overflow)
            if name == "development":
                dev_n -= take
            elif name == "hidden":
                hidden_n -= take
            else:
                prospective_n -= take
            overflow -= take
        if overflow > 0:
            train_n = max(1, train_n - overflow)
    elif allocated < n:
        hidden_n += n - allocated

    train_end = train_n
    dev_end = train_end + dev_n
    hidden_end = dev_end + hidden_n
    prospective_end = hidden_end + prospective_n
    return {
        "training": ordered[:train_end],
        "development": ordered[train_end:dev_end],
        "hidden": ordered[dev_end:hidden_end],
        "prospective": ordered[hidden_end:prospective_end],
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
