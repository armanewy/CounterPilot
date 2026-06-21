from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import median
from typing import Any


@dataclass
class BaselineResult:
    model_id: str
    features_used: list[str]
    predictions: list[dict[str, Any]]


class MajorityClassifier:
    def __init__(self, *, model_id: str = "majority") -> None:
        self.model_id = model_id
        self.label = ""
        self.labels: list[str] = []

    def fit(self, rows: list[dict[str, Any]]) -> "MajorityClassifier":
        counts = Counter(str(row["label"]) for row in rows)
        self.labels = sorted(counts)
        self.label = counts.most_common(1)[0][0] if counts else ""
        return self

    def predict(self, rows: list[dict[str, Any]]) -> BaselineResult:
        probability = 1.0 / max(len(self.labels), 1)
        predictions = [
            {
                "row_id": row["row_id"],
                "label": row["label"],
                "prediction": self.label,
                "probabilities": {label: probability for label in self.labels},
                "split": row.get("split", "unknown"),
            }
            for row in rows
        ]
        return BaselineResult(self.model_id, [], predictions)


class CategoryMajorityClassifier:
    def __init__(self) -> None:
        self.global_model = MajorityClassifier(model_id="category_majority_global")
        self.by_category: dict[str, str] = {}
        self.labels: list[str] = []

    def fit(self, rows: list[dict[str, Any]]) -> "CategoryMajorityClassifier":
        self.global_model.fit(rows)
        self.labels = self.global_model.labels
        grouped: dict[str, Counter[str]] = defaultdict(Counter)
        for row in rows:
            grouped[str(row["features"].get("category"))][str(row["label"])] += 1
        self.by_category = {category: counts.most_common(1)[0][0] for category, counts in grouped.items()}
        return self

    def predict(self, rows: list[dict[str, Any]]) -> BaselineResult:
        probability = 1.0 / max(len(self.labels), 1)
        predictions = []
        for row in rows:
            label = self.by_category.get(str(row["features"].get("category")), self.global_model.label)
            predictions.append(
                {
                    "row_id": row["row_id"],
                    "label": row["label"],
                    "prediction": label,
                    "probabilities": {item: probability for item in self.labels},
                    "split": row.get("split", "unknown"),
                }
            )
        return BaselineResult("category_majority", ["category"], predictions)


class OfferRatioThresholdClassifier:
    def __init__(self, *, accept_threshold: float = 0.85, counter_threshold: float = 0.65) -> None:
        self.accept_threshold = accept_threshold
        self.counter_threshold = counter_threshold

    def fit(self, rows: list[dict[str, Any]]) -> "OfferRatioThresholdClassifier":
        return self

    def predict(self, rows: list[dict[str, Any]]) -> BaselineResult:
        predictions = []
        for row in rows:
            ratio = row["features"].get("offer_to_asking_ratio")
            value = float(ratio or 0.0)
            if value >= self.accept_threshold:
                label = "accept"
            elif value >= self.counter_threshold:
                label = "counter"
            else:
                label = "decline"
            predictions.append(
                {
                    "row_id": row["row_id"],
                    "label": row["label"],
                    "prediction": label,
                    "probabilities": {"accept": 0.34, "counter": 0.33, "decline": 0.33},
                    "split": row.get("split", "unknown"),
                }
            )
        return BaselineResult("offer_ratio_threshold", ["offer_to_asking_ratio"], predictions)


class MedianRegressor:
    def __init__(self, *, model_id: str = "median_regressor") -> None:
        self.model_id = model_id
        self.value = 0.0

    def fit(self, rows: list[dict[str, Any]]) -> "MedianRegressor":
        labels = [float(row["label"]) for row in rows]
        self.value = median(labels) if labels else 0.0
        return self

    def predict(self, rows: list[dict[str, Any]]) -> BaselineResult:
        return BaselineResult(
            self.model_id,
            [],
            [
                {
                    "row_id": row["row_id"],
                    "label": row["label"],
                    "prediction": self.value,
                    "split": row.get("split", "unknown"),
                }
                for row in rows
            ],
        )
