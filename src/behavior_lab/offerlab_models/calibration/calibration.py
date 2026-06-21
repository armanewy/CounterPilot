from __future__ import annotations

from collections import Counter, defaultdict
import math
import random
from statistics import median
from typing import Any

from behavior_lab.offerlab_models.common import enriched_features, support_abstention_report


def sigmoid_calibrate(probabilities: list[float], outcomes: list[int], *, iterations: int = 200, learning_rate: float = 0.1) -> dict[str, Any]:
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have same length")
    a = 1.0
    b = 0.0
    logits = [_logit(value) for value in probabilities]
    for _ in range(iterations):
        grad_a = 0.0
        grad_b = 0.0
        for logit, outcome in zip(logits, outcomes):
            prediction = _sigmoid(a * logit + b)
            error = prediction - float(outcome)
            grad_a += error * logit
            grad_b += error
        scale = max(1, len(probabilities))
        a -= learning_rate * grad_a / scale
        b -= learning_rate * grad_b / scale
    calibrated = [_sigmoid(a * logit + b) for logit in logits]
    return {"method": "sigmoid", "a": a, "b": b, "calibrated": calibrated}


def isotonic_calibrate(probabilities: list[float], outcomes: list[int]) -> dict[str, Any]:
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have same length")
    ordered = sorted(enumerate(zip(probabilities, outcomes)), key=lambda item: item[1][0])
    blocks = []
    for original_index, (probability, outcome) in ordered:
        blocks.append({"value": float(outcome), "weight": 1.0, "indices": [original_index], "min_probability": probability, "max_probability": probability})
        index = len(blocks) - 1
        while index > 0 and blocks[index - 1]["value"] > blocks[index]["value"]:
            left = blocks[index - 1]
            right = blocks[index]
            weight = left["weight"] + right["weight"]
            merged = {
                "value": (left["value"] * left["weight"] + right["value"] * right["weight"]) / weight,
                "weight": weight,
                "indices": left["indices"] + right["indices"],
                "min_probability": left["min_probability"],
                "max_probability": right["max_probability"],
            }
            blocks[index - 1 : index + 1] = [merged]
            index -= 1
    calibrated = [0.0] * len(probabilities)
    for block in blocks:
        for index in block["indices"]:
            calibrated[index] = float(block["value"])
    return {
        "method": "isotonic",
        "calibrated": calibrated,
        "blocks": [
            {
                "min_probability": block["min_probability"],
                "max_probability": block["max_probability"],
                "calibrated_probability": block["value"],
                "count": len(block["indices"]),
            }
            for block in blocks
        ],
    }


def reliability_diagram(predictions: list[dict[str, Any]], *, positive_label: str = "accept", bins: int = 10) -> dict[str, Any]:
    buckets = [{"count": 0, "predicted_sum": 0.0, "observed_sum": 0.0} for _ in range(bins)]
    for row in predictions:
        probability = float(row.get("probability", row.get("probabilities", {}).get(positive_label, 0.0)))
        index = min(bins - 1, max(0, int(probability * bins)))
        buckets[index]["count"] += 1
        buckets[index]["predicted_sum"] += probability
        buckets[index]["observed_sum"] += 1.0 if str(row.get("label")) == positive_label else 0.0
    output = []
    expected_calibration_error = 0.0
    total = sum(bucket["count"] for bucket in buckets)
    for index, bucket in enumerate(buckets):
        count = bucket["count"]
        mean_prediction = bucket["predicted_sum"] / count if count else 0.0
        empirical_rate = bucket["observed_sum"] / count if count else 0.0
        if total:
            expected_calibration_error += count / total * abs(mean_prediction - empirical_rate)
        output.append({"bin": index, "count": count, "mean_prediction": mean_prediction, "empirical_rate": empirical_rate})
    return {"bins": output, "expected_calibration_error": expected_calibration_error}


def calibration_by_slices(predictions: list[dict[str, Any]], rows: list[dict[str, Any]], *, positive_label: str = "accept") -> dict[str, Any]:
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_offer_range: dict[str, list[dict[str, Any]]] = defaultdict(list)
    row_lookup = {row["row_id"]: row for row in rows}
    for prediction in predictions:
        source = row_lookup.get(prediction["row_id"], {})
        features = enriched_features(source)
        by_category[str(features.get("category", "missing"))].append(prediction)
        ratio = float(features.get("offer_to_asking_ratio") or 0.0)
        if ratio < 0.7:
            band = "low_offer"
        elif ratio < 0.85:
            band = "middle_offer"
        else:
            band = "high_offer"
        by_offer_range[band].append(prediction)
    return {
        "category": {key: reliability_diagram(value, positive_label=positive_label, bins=5) for key, value in by_category.items()},
        "offer_range": {key: reliability_diagram(value, positive_label=positive_label, bins=5) for key, value in by_offer_range.items()},
    }


def temporal_drift(predictions: list[dict[str, Any]], rows: list[dict[str, Any]], *, positive_label: str = "accept") -> dict[str, Any]:
    row_lookup = {row["row_id"]: row for row in rows}
    ordered = sorted(predictions, key=lambda item: row_lookup.get(item["row_id"], {}).get("timestamp", ""))
    midpoint = len(ordered) // 2
    early = reliability_diagram(ordered[:midpoint], positive_label=positive_label, bins=5)
    late = reliability_diagram(ordered[midpoint:], positive_label=positive_label, bins=5)
    return {
        "early_ece": early["expected_calibration_error"],
        "late_ece": late["expected_calibration_error"],
        "absolute_drift": abs(late["expected_calibration_error"] - early["expected_calibration_error"]),
        "early_rows": len(ordered[:midpoint]),
        "late_rows": len(ordered[midpoint:]),
    }


def bootstrap_brier_uncertainty(predictions: list[dict[str, Any]], *, positive_label: str = "accept", samples: int = 200, seed: int = 7) -> dict[str, Any]:
    if not predictions:
        return {"mean_brier": 0.0, "confidence_interval": [0.0, 0.0], "samples": 0}
    rng = random.Random(seed)
    scores = []
    for _ in range(samples):
        draw = [rng.choice(predictions) for _ in predictions]
        scores.append(_brier(draw, positive_label=positive_label))
    ordered = sorted(scores)
    return {
        "mean_brier": sum(scores) / len(scores),
        "confidence_interval": [ordered[int(0.025 * (len(ordered) - 1))], ordered[int(0.975 * (len(ordered) - 1))]],
        "samples": samples,
    }


def action_level_sample_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(row["label"]) for row in rows).items()))


def support_abstention(train_rows: list[dict[str, Any]], eval_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return support_abstention_report(train_rows, eval_rows)


def final_price_prediction_interval(train_rows: list[dict[str, Any]], prediction: float, *, confidence: float = 0.8) -> dict[str, Any]:
    residuals = sorted(abs(float(row["label"]) - prediction) for row in train_rows)
    if not residuals:
        return {"prediction": prediction, "lower": prediction, "upper": prediction, "confidence": confidence}
    index = min(len(residuals) - 1, max(0, round((len(residuals) - 1) * confidence)))
    radius = residuals[index]
    return {"prediction": prediction, "lower": prediction - radius, "upper": prediction + radius, "confidence": confidence, "residual_median": median(residuals)}


def _brier(predictions: list[dict[str, Any]], *, positive_label: str) -> float:
    total = 0.0
    for row in predictions:
        probability = float(row.get("probability", row.get("probabilities", {}).get(positive_label, 0.0)))
        observed = 1.0 if str(row.get("label")) == positive_label else 0.0
        total += (probability - observed) ** 2
    return total / len(predictions) if predictions else 0.0


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, value))))


def _logit(value: float) -> float:
    probability = min(1.0 - 1e-6, max(1e-6, value))
    return math.log(probability / (1.0 - probability))
