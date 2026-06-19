from __future__ import annotations

from typing import Any, Protocol


class Hypothesis(Protocol):
    def fit(self, training_data: list[dict[str, Any]]) -> "FittedHypothesis":
        ...


class FittedHypothesis(Protocol):
    model_id: str
    complexity: int

    def predict_action(self, context: dict[str, Any], actions: list[str]) -> dict[str, float]:
        ...

    def predict_outcome(self, context: dict[str, Any], intervention: str) -> dict[str, float]:
        ...

    def simulate(self, initial_state: dict[str, Any], intervention: str, horizon: int) -> list[dict[str, Any]]:
        ...
