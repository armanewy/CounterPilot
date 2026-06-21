from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Callable


@dataclass(frozen=True)
class OpeEstimate:
    estimator: str
    value: float
    effective_sample_size: float | None
    support_violations: int
    confidence_interval: tuple[float, float]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["confidence_interval"] = list(self.confidence_interval)
        return payload


Policy = Callable[[dict[str, object]], dict[str, float]]


def ips(logs: list[dict[str, object]], policy: Policy) -> OpeEstimate:
    weighted_rewards = []
    weights = []
    support_violations = 0
    for row in logs:
        action = str(row["action"])
        propensity = float(row["propensity"])
        target_probability = policy(row).get(action, 0.0)
        if propensity <= 0 or target_probability <= 0:
            support_violations += 1
            weights.append(0.0)
            weighted_rewards.append(0.0)
            continue
        weight = target_probability / propensity
        weights.append(weight)
        weighted_rewards.append(weight * float(row["reward"]))
    value = sum(weighted_rewards) / len(logs) if logs else 0.0
    return OpeEstimate("ips", value, _effective_sample_size(weights), support_violations, _normal_ci(weighted_rewards))


def self_normalized_ips(logs: list[dict[str, object]], policy: Policy) -> OpeEstimate:
    weighted_rewards = []
    weights = []
    support_violations = 0
    for row in logs:
        action = str(row["action"])
        propensity = float(row["propensity"])
        target_probability = policy(row).get(action, 0.0)
        if propensity <= 0 or target_probability <= 0:
            support_violations += 1
            weights.append(0.0)
            weighted_rewards.append(0.0)
            continue
        weight = target_probability / propensity
        weights.append(weight)
        weighted_rewards.append(weight * float(row["reward"]))
    total_weight = sum(weights)
    value = sum(weighted_rewards) / total_weight if total_weight else 0.0
    return OpeEstimate("self_normalized_ips", value, _effective_sample_size(weights), support_violations, _normal_ci(weighted_rewards))


def direct_method(logs: list[dict[str, object]], policy: Policy) -> OpeEstimate:
    reward_by_action: dict[str, list[float]] = {}
    for row in logs:
        reward_by_action.setdefault(str(row["action"]), []).append(float(row["reward"]))
    mean_by_action = {action: sum(values) / len(values) for action, values in reward_by_action.items()}
    values = []
    support_violations = 0
    for row in logs:
        probabilities = policy(row)
        estimate = 0.0
        for action, probability in probabilities.items():
            if action not in mean_by_action:
                support_violations += 1
                continue
            estimate += probability * mean_by_action[action]
        values.append(estimate)
    return OpeEstimate("direct_method", sum(values) / len(values) if values else 0.0, None, support_violations, _normal_ci(values))


def doubly_robust(logs: list[dict[str, object]], policy: Policy) -> OpeEstimate:
    dm = direct_method(logs, policy)
    reward_by_action: dict[str, list[float]] = {}
    for row in logs:
        reward_by_action.setdefault(str(row["action"]), []).append(float(row["reward"]))
    mean_by_action = {action: sum(values) / len(values) for action, values in reward_by_action.items()}
    values = []
    weights = []
    support_violations = 0
    for row in logs:
        action = str(row["action"])
        propensity = float(row["propensity"])
        probabilities = policy(row)
        target_probability = probabilities.get(action, 0.0)
        if propensity <= 0:
            support_violations += 1
            continue
        model_value = sum(probability * mean_by_action.get(candidate, 0.0) for candidate, probability in probabilities.items())
        correction = target_probability / propensity * (float(row["reward"]) - mean_by_action.get(action, 0.0))
        weights.append(target_probability / propensity)
        values.append(model_value + correction)
    return OpeEstimate("doubly_robust", sum(values) / len(values) if values else dm.value, _effective_sample_size(weights), support_violations, _normal_ci(values))


def evaluate_policy(logs: list[dict[str, object]], policy: Policy) -> dict[str, object]:
    estimates = [direct_method(logs, policy), ips(logs, policy), self_normalized_ips(logs, policy), doubly_robust(logs, policy)]
    on_policy = sum(float(row["reward"]) for row in logs) / len(logs) if logs else 0.0
    return {"source_id": "open_bandit_dataset", "on_policy_value": on_policy, "estimates": [estimate.to_dict() for estimate in estimates]}


def _effective_sample_size(weights: list[float]) -> float:
    total = sum(weights)
    squared = sum(weight * weight for weight in weights)
    return (total * total / squared) if squared else 0.0


def _normal_ci(values: list[float]) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    mean = sum(values) / len(values)
    if len(values) == 1:
        return (mean, mean)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    stderr = math.sqrt(variance / len(values))
    return (mean - 1.96 * stderr, mean + 1.96 * stderr)
