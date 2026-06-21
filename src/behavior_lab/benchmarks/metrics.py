from __future__ import annotations

import math
from typing import Any, Iterable


def classification_accuracy(rows: Iterable[dict[str, Any]]) -> float:
    items = list(rows)
    if not items:
        return 0.0
    return sum(1 for row in items if row["label"] == row["prediction"]) / len(items)


def multiclass_log_loss(rows: Iterable[dict[str, Any]], *, labels: list[str] | None = None) -> float:
    items = list(rows)
    if not items:
        return 0.0
    known = labels or sorted({str(row["label"]) for row in items})
    epsilon = 1e-12
    total = 0.0
    for row in items:
        probabilities = row.get("probabilities")
        if isinstance(probabilities, dict):
            probability = float(probabilities.get(str(row["label"]), epsilon))
        else:
            probability = 1.0 / max(len(known), 1)
        total -= math.log(max(min(probability, 1.0 - epsilon), epsilon))
    return total / len(items)


def brier_score(rows: Iterable[dict[str, Any]], *, positive_label: str = "1") -> float:
    items = list(rows)
    if not items:
        return 0.0
    total = 0.0
    for row in items:
        probability = float(row.get("probability", row.get("probabilities", {}).get(positive_label, 0.0)))
        observed = 1.0 if str(row["label"]) == positive_label else 0.0
        total += (probability - observed) ** 2
    return total / len(items)


def regression_rmse(rows: Iterable[dict[str, Any]]) -> float:
    items = list(rows)
    if not items:
        return 0.0
    return math.sqrt(sum((float(row["prediction"]) - float(row["label"])) ** 2 for row in items) / len(items))


def calibration_bins(rows: Iterable[dict[str, Any]], *, positive_label: str = "1", bins: int = 10) -> list[dict[str, Any]]:
    buckets = [{"count": 0, "probability_sum": 0.0, "observed_sum": 0.0} for _ in range(bins)]
    for row in rows:
        probability = float(row.get("probability", row.get("probabilities", {}).get(positive_label, 0.0)))
        index = min(bins - 1, max(0, int(probability * bins)))
        buckets[index]["count"] += 1
        buckets[index]["probability_sum"] += probability
        buckets[index]["observed_sum"] += 1.0 if str(row["label"]) == positive_label else 0.0
    output = []
    for index, bucket in enumerate(buckets):
        count = int(bucket["count"])
        output.append(
            {
                "bin": index,
                "count": count,
                "mean_prediction": bucket["probability_sum"] / count if count else 0.0,
                "empirical_rate": bucket["observed_sum"] / count if count else 0.0,
            }
        )
    return output
