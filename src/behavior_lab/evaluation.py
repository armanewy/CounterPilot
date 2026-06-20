from __future__ import annotations

from dataclasses import asdict
import math
from statistics import mean, pstdev
from typing import Any, Protocol

from behavior_lab.core import EvaluationMetrics


class InvalidPredictionError(ValueError):
    pass


class BinaryPredictor(Protocol):
    model_id: str
    complexity: int

    def predict_proba(self, features: dict[str, Any]) -> float:
        ...


def clamp_probability(value: float) -> float:
    probability = float(value)
    if not math.isfinite(probability):
        raise InvalidPredictionError(f"Model returned a non-finite probability: {value!r}")
    return min(max(probability, 1e-6), 1.0 - 1e-6)


def log_loss_one(probability: float, target: int) -> float:
    p = clamp_probability(probability)
    return -(target * math.log(p) + (1 - target) * math.log(1 - p))


def brier_one(probability: float, target: int) -> float:
    p = clamp_probability(probability)
    return (p - target) ** 2


def calibration_error(predictions: list[float], targets: list[int], bins: int = 10) -> float:
    if not predictions:
        return 0.0
    total = 0.0
    for bucket in range(bins):
        low = bucket / bins
        high = (bucket + 1) / bins
        indices = [
            index
            for index, prediction in enumerate(predictions)
            if (low <= prediction < high) or (bucket == bins - 1 and prediction == 1.0)
        ]
        if not indices:
            continue
        avg_prediction = mean(predictions[index] for index in indices)
        avg_target = mean(targets[index] for index in indices)
        total += (len(indices) / len(predictions)) * abs(avg_prediction - avg_target)
    return total


def evaluate_model(
    model: BinaryPredictor,
    rows: list[dict[str, Any]],
    *,
    split: str,
    include_details: bool = False,
) -> EvaluationMetrics:
    predictions = [clamp_probability(model.predict_proba(row["features"])) for row in rows]
    targets = [int(row["target"]) for row in rows]
    if not rows:
        base_rate = 0.5
        model_loss = 0.0
        base_loss = 0.0
        brier = 0.0
        calibration = 0.0
    else:
        base_rate = clamp_probability(mean(targets))
        losses = [log_loss_one(prediction, target) for prediction, target in zip(predictions, targets, strict=True)]
        model_loss = mean(losses)
        base_loss = mean(log_loss_one(base_rate, target) for target in targets)
        brier = mean(brier_one(prediction, target) for prediction, target in zip(predictions, targets, strict=True))
        calibration = calibration_error(predictions, targets)

    details: dict[str, Any] = {}
    if include_details:
        details["residuals"] = residuals(model, rows, limit=8)
        details["prediction_summary"] = {
            "min": min(predictions) if predictions else None,
            "max": max(predictions) if predictions else None,
            "mean": mean(predictions) if predictions else None,
        }

    return EvaluationMetrics(
        model_id=model.model_id,
        split=split,
        n=len(rows),
        log_loss=model_loss,
        brier_score=brier,
        calibration_error=calibration,
        base_rate=base_rate,
        lift_over_base_log_loss=base_loss - model_loss,
        complexity=getattr(model, "complexity", 0),
        details=details,
    )


def residuals(model: BinaryPredictor, rows: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    scored = []
    for row in rows:
        prediction = clamp_probability(model.predict_proba(row["features"]))
        target = int(row["target"])
        scored.append(
            {
                "case_id": row["case_id"],
                "prediction": prediction,
                "target": target,
                "absolute_error": abs(prediction - target),
                "features": dict(row["features"]),
            }
        )
    scored.sort(key=lambda item: item["absolute_error"], reverse=True)
    return scored[:limit]


def counterexamples(
    model_a: BinaryPredictor,
    model_b: BinaryPredictor,
    rows: list[dict[str, Any]],
    limit: int = 10,
) -> list[dict[str, Any]]:
    disagreements = []
    for row in rows:
        pa = clamp_probability(model_a.predict_proba(row["features"]))
        pb = clamp_probability(model_b.predict_proba(row["features"]))
        disagreements.append(
            {
                "case_id": row["case_id"],
                "model_a": model_a.model_id,
                "model_b": model_b.model_id,
                "prediction_a": pa,
                "prediction_b": pb,
                "gap": abs(pa - pb),
                "target": int(row["target"]),
                "features": dict(row["features"]),
            }
        )
    disagreements.sort(key=lambda item: item["gap"], reverse=True)
    return disagreements[:limit]


def paired_compare(
    model_a: BinaryPredictor,
    model_b: BinaryPredictor,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    deltas = []
    wins = 0
    for row in rows:
        target = int(row["target"])
        loss_a = log_loss_one(model_a.predict_proba(row["features"]), target)
        loss_b = log_loss_one(model_b.predict_proba(row["features"]), target)
        delta = loss_a - loss_b
        deltas.append(delta)
        wins += 1 if delta > 0 else 0
    if not deltas:
        return {"n": 0, "mean_paired_improvement": 0.0, "uncertainty_interval": [0.0, 0.0], "b_wins": 0.0}
    avg = mean(deltas)
    se = pstdev(deltas) / math.sqrt(len(deltas)) if len(deltas) > 1 else 0.0
    return {
        "n": len(deltas),
        "model_a": model_a.model_id,
        "model_b": model_b.model_id,
        "mean_paired_improvement": avg,
        "uncertainty_interval": [avg - 1.96 * se, avg + 1.96 * se],
        "b_wins": wins / len(deltas),
    }


def pareto_frontier(metrics: list[EvaluationMetrics]) -> list[dict[str, Any]]:
    frontier: list[EvaluationMetrics] = []
    for candidate in metrics:
        dominated = False
        for other in metrics:
            if other is candidate:
                continue
            no_worse = other.log_loss <= candidate.log_loss and other.complexity <= candidate.complexity
            strictly_better = other.log_loss < candidate.log_loss or other.complexity < candidate.complexity
            if no_worse and strictly_better:
                dominated = True
                break
        if not dominated:
            frontier.append(candidate)
    frontier.sort(key=lambda item: (item.log_loss, item.complexity))
    return [asdict(item) for item in frontier]
