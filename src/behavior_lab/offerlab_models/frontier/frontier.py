from __future__ import annotations

from collections import Counter
from typing import Any

from behavior_lab.core import stable_hash
from behavior_lab.offerlab_models.common import PRODUCTION_EXPORT_ALLOWED, SOURCE_ID, enriched_features, model_lineage


def counteroffer_frontier(context: dict[str, Any], historical_rows: list[dict[str, Any]], counter_amounts: list[float], *, k: int = 5) -> dict[str, Any]:
    listing_price = _listing_price(context)
    if listing_price <= 0:
        raise ValueError("context must contain a positive listing_price")
    observed = [
        {
            "row": row,
            "ratio": float(enriched_features(row).get("offer_to_asking_ratio") or 0.0),
            "category": str(enriched_features(row).get("category", "missing")),
        }
        for row in historical_rows
    ]
    ratios = [item["ratio"] for item in observed if item["ratio"] > 0]
    support_range = {"min_offer_ratio": min(ratios) if ratios else 0.0, "max_offer_ratio": max(ratios) if ratios else 0.0}
    context_category = str(enriched_features(context).get("category", "missing"))
    category_observed = [item for item in observed if item["category"] == context_category]
    category_ratios = [item["ratio"] for item in category_observed if item["ratio"] > 0]
    category_support_range = {
        "min_offer_ratio": min(category_ratios) if category_ratios else 0.0,
        "max_offer_ratio": max(category_ratios) if category_ratios else 0.0,
        "category": context_category,
        "count": len(category_ratios),
    }
    frontier = []
    for amount in counter_amounts:
        ratio = amount / listing_price
        if not category_ratios or ratio < category_support_range["min_offer_ratio"] or ratio > category_support_range["max_offer_ratio"]:
            frontier.append(
                {
                    "counter_amount": amount,
                    "offer_to_asking_ratio": ratio,
                    "supported": False,
                    "rejection_reason": "candidate counter is outside same-category observed support",
                    "nearest_comparable_counters": [],
                }
            )
            continue
        comparables = sorted(
            category_observed,
            key=lambda item: abs(item["ratio"] - ratio),
        )[:k]
        counts = Counter(_buyer_state(item["row"]) for item in comparables)
        total = sum(counts.values()) or 1
        probabilities = {
            "buyer_accept": counts["accept"] / total,
            "buyer_counter": counts["counter"] / total,
            "buyer_exit": counts["exit"] / total,
        }
        frontier.append(
            {
                "counter_amount": amount,
                "offer_to_asking_ratio": ratio,
                "supported": True,
                "buyer_response_probabilities": probabilities,
                "expected_rounds": 1.0 + probabilities["buyer_counter"],
                "uncertainty": {
                    "comparable_count": len(comparables),
                    "response_entropy": _entropy(list(probabilities.values())),
                },
                "nearest_comparable_counters": [
                    {
                        "case_token": stable_hash(
                            {
                                "timestamp": item["row"].get("timestamp"),
                                "features": item["row"].get("features", {}),
                            }
                        )[:16],
                        "ratio": item["ratio"],
                    }
                    for item in comparables
                ],
            }
        )
    return {
        "evidence_role": "PREDICTIVE_FRONTIER_RESEARCH",
        "source_id": SOURCE_ID,
        "production_export_allowed": PRODUCTION_EXPORT_ALLOWED,
        "causal_claim": False,
        "profit_optimization": False,
        "historical_rows_scope": "caller_supplied_training_rows_only",
        "support_range": support_range,
        "same_category_support_range": category_support_range,
        "frontier": frontier,
        "lineage": model_lineage("predictive_counteroffer_frontier", historical_rows, feature_contract=["category", "offer_to_asking_ratio"]),
        "warning": "Frontier is predictive and support-bound; it is not causal profit optimization.",
    }


def _buyer_state(row: dict[str, Any]) -> str:
    label = str(row.get("label", "")).lower()
    if label == "accept":
        return "accept"
    if label == "counter":
        return "counter"
    return "exit"


def _listing_price(row: dict[str, Any]) -> float:
    if "features" in row:
        return float(row["features"].get("listing_price") or 0.0)
    return float(row.get("listing_price") or 0.0)


def _entropy(values: list[float]) -> float:
    import math

    return -sum(value * math.log(value, 2) for value in values if value > 0)
