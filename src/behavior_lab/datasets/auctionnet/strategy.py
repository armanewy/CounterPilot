from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Protocol


class Strategy(Protocol):
    name: str

    def bid(self, value: float, step: int, budget_remaining: float) -> float:
        ...


@dataclass(frozen=True)
class StrategyReport:
    strategy: str
    reward: float
    spend: float
    budget_remaining: float
    regret: float
    reward_variance: float
    simulation_only: bool = True

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LinearStrategy:
    name: str
    multiplier: float

    def bid(self, value: float, step: int, budget_remaining: float) -> float:
        return min(max(value * self.multiplier, 0.0), budget_remaining)


def deterministic_environment() -> list[dict[str, float]]:
    return [
        {"value": 10.0, "market_price": 4.0},
        {"value": 8.0, "market_price": 5.0},
        {"value": 6.0, "market_price": 7.0},
        {"value": 12.0, "market_price": 9.0},
        {"value": 7.0, "market_price": 3.0},
    ]


def compare_strategies(*, budget: float = 20.0) -> dict[str, object]:
    strategies: list[Strategy] = [
        LinearStrategy("fixed_policy", 0.6),
        LinearStrategy("conservative_policy", 0.45),
        LinearStrategy("learned_policy", 0.72),
        LinearStrategy("over_aggressive_policy", 1.1),
    ]
    environment = deterministic_environment()
    best_possible = _oracle_reward(environment, budget)
    reports = [_run_strategy(strategy, environment, budget, best_possible).to_dict() for strategy in strategies]
    return {
        "source_id": "auctionnet",
        "evidence_role": "SIMULATION",
        "simulation_only": True,
        "warning": "Do not use this result as evidence about real eBay buyer acceptance rates.",
        "reports": reports,
    }


def _run_strategy(strategy: Strategy, environment: list[dict[str, float]], budget: float, best_possible: float) -> StrategyReport:
    remaining = budget
    rewards = []
    spend = 0.0
    for step, auction in enumerate(environment):
        bid = strategy.bid(auction["value"], step, remaining)
        if bid >= auction["market_price"] and remaining >= auction["market_price"]:
            remaining -= auction["market_price"]
            spend += auction["market_price"]
            rewards.append(auction["value"] - auction["market_price"])
        else:
            rewards.append(0.0)
    reward = sum(rewards)
    return StrategyReport(
        strategy=strategy.name,
        reward=round(reward, 4),
        spend=round(spend, 4),
        budget_remaining=round(remaining, 4),
        regret=round(best_possible - reward, 4),
        reward_variance=round(_variance(rewards), 6),
    )


def _oracle_reward(environment: list[dict[str, float]], budget: float) -> float:
    remaining = budget
    reward = 0.0
    for auction in sorted(environment, key=lambda item: item["value"] - item["market_price"], reverse=True):
        if auction["value"] > auction["market_price"] and remaining >= auction["market_price"]:
            remaining -= auction["market_price"]
            reward += auction["value"] - auction["market_price"]
    return reward


def _variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    center = mean(values)
    return sum((value - center) ** 2 for value in values) / (len(values) - 1)
