from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import math
import random
from typing import Any

from behavior_lab.core import DecisionEpisode, InterventionTrial, new_id


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(min(value, 50.0), -50.0)))


class HiddenWorld:
    name = "base"
    hidden_drivers: set[str] = set()

    def __init__(self, seed: int = 7, subject_id: str = "synthetic_subject"):
        self.random = random.Random(seed)
        self.seed = seed
        self.subject_id = subject_id
        self._episode_index = 0

    def sample_context(self) -> dict[str, Any]:
        sleep_hours = self.random.uniform(4.5, 8.5)
        fatigue = max(0.0, min(1.0, (7.8 - sleep_hours) / 4.0 + self.random.uniform(-0.1, 0.12)))
        ambiguity = self.random.betavariate(2.0, 2.2)
        duration = self.random.choice([15, 30, 45, 60, 90, 120, 180])
        deadline_distance_hours = self.random.choice([1, 3, 8, 24, 72, 168])
        deadline_near = 1.0 if deadline_distance_hours <= 8 else 0.0
        public_commitment = self.random.random() < 0.22
        explicit_first_step = self.random.random() < 0.35
        recent_context_switches = self.random.randint(0, 18)
        time_of_day = self.random.choice(["morning", "afternoon", "evening", "late"])
        task_size = "large" if duration >= 90 else "medium" if duration >= 45 else "small"
        previous_task_success = self.random.random() < 0.55
        social_cost = self.random.random()
        repeated_failures = self.random.randint(0, 5)
        importance = self.random.random()
        return {
            "sleep_hours": round(sleep_hours, 2),
            "fatigue": round(fatigue, 3),
            "ambiguity": round(ambiguity, 3),
            "estimated_duration_minutes": duration,
            "deadline_distance_hours": deadline_distance_hours,
            "deadline_near": deadline_near,
            "public_commitment": 1.0 if public_commitment else 0.0,
            "explicit_first_step": 1.0 if explicit_first_step else 0.0,
            "recent_context_switches": recent_context_switches,
            "time_of_day_morning": 1.0 if time_of_day == "morning" else 0.0,
            "time_of_day_evening": 1.0 if time_of_day == "evening" else 0.0,
            "task_size_large": 1.0 if task_size == "large" else 0.0,
            "previous_task_success": 1.0 if previous_task_success else 0.0,
            "social_cost": round(social_cost, 3),
            "repeated_failures": repeated_failures,
            "importance": round(importance, 3),
        }

    def probability_start(self, context: dict[str, Any]) -> float:
        return sigmoid(-0.5)

    def mechanism_equivalence_score(self, terms: list[str]) -> float:
        if not self.hidden_drivers:
            return 0.0
        text = " ".join(terms)
        matched = sum(1 for driver in self.hidden_drivers if driver in text)
        return matched / len(self.hidden_drivers)

    def generate_episode(self, context: dict[str, Any] | None = None) -> DecisionEpisode:
        self._episode_index += 1
        context = dict(context or self.sample_context())
        probability = self.probability_start(context)
        started = self.random.random() < probability
        action = "start_now" if started else self.random.choice(["prepare_without_starting", "switch_task", "defer"])
        latency = int(self.random.expovariate(1 / 260)) if started else int(self.random.expovariate(1 / 900) + 600)
        decision_time = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=30 * self._episode_index)
        return DecisionEpisode.create(
            subject_id=self.subject_id,
            decision_time=decision_time.isoformat(),
            observation_cutoff=(decision_time - timedelta(seconds=1)).isoformat(),
            situation={
                "type": "start_planned_task",
                "description": f"synthetic task {self._episode_index}",
                "world": self.name,
            },
            available_actions=["start_now", "prepare_without_starting", "switch_task", "defer", "abandon"],
            pre_decision_context=context,
            observed_action={"action": action, "latency_seconds": latency},
            later_outcomes={
                "started_within_10_minutes": bool(started and latency <= 600),
                "started_within_2_hours": bool(started or self.random.random() < 0.35),
                "completed_within_day": bool(started and self.random.random() < 0.55),
            },
            data_provenance={
                "world": self.name,
                "mechanism_hidden_from_llm": True,
                "random_seed": self.seed,
            },
        )

    def generate_dataset(self, episodes: int) -> list[DecisionEpisode]:
        return [self.generate_episode() for _ in range(episodes)]

    def run_intervention_trial(
        self,
        context: dict[str, Any],
        treatment: str,
        comparator: str,
        assigned_treatment: str,
        probability: float,
        preregistration_id: str | None = None,
    ) -> InterventionTrial:
        intervened = dict(context)
        if assigned_treatment == "explicit_first_step":
            intervened["explicit_first_step"] = 1.0
        elif assigned_treatment in {"generic_task_description", "no_intervention"}:
            intervened["explicit_first_step"] = 0.0
        elif assigned_treatment == "visible_commitment":
            intervened["public_commitment"] = 1.0
        elif assigned_treatment == "two_minute_countdown":
            intervened["deadline_near"] = 1.0
        probability_start = self.probability_start(intervened)
        started = self.random.random() < probability_start
        latency = int(self.random.expovariate(1 / 220)) if started else int(self.random.expovariate(1 / 900) + 650)
        return InterventionTrial.create(
            subject_id=self.subject_id,
            context_snapshot_id=new_id("c"),
            comparison={"treatment": treatment, "comparator": comparator},
            assignment={
                "method": "randomized_block",
                "assigned_treatment": assigned_treatment,
                "probability": probability,
                "block": {
                    "fatigue_band": "high" if context.get("fatigue", 0.0) > 0.66 else "medium",
                    "task_size": "large" if context.get("task_size_large", 0.0) else "small_or_medium",
                },
            },
            adherence={"treatment_delivered": True, "treatment_seen": True},
            outcomes={
                "started_within_10_minutes": bool(started and latency <= 600),
                "time_to_start_seconds": latency,
                "completed_within_day": bool(started and self.random.random() < 0.55),
            },
            measurement_horizons=["10_minutes", "2_hours", "1_day"],
            preregistration_id=preregistration_id,
            data_provenance={
                "world": self.name,
                "offline_synthetic_trial": True,
                "context_snapshot": context,
                "intervened_context": intervened,
            },
        )


class HabitPlusOverrideWorld(HiddenWorld):
    name = "habit_plus_override"
    hidden_drivers = {
        "deadline_near",
        "public_commitment",
        "fatigue",
        "explicit_first_step",
        "ambiguity",
        "recent_context_switches",
    }

    def probability_start(self, context: dict[str, Any]) -> float:
        ambiguity_high = 1.0 if context.get("ambiguity", 0.0) > 0.6 else 0.0
        logit = (
            -0.75
            + 2.0 * context.get("deadline_near", 0.0)
            + 1.2 * context.get("public_commitment", 0.0)
            - 1.5 * context.get("fatigue", 0.0)
            + 0.9 * context.get("explicit_first_step", 0.0) * ambiguity_high
            - 0.055 * context.get("recent_context_switches", 0.0)
        )
        return sigmoid(logit)


class TwoModePersonWorld(HiddenWorld):
    name = "two_mode_person"
    hidden_drivers = {"sleep_hours", "previous_task_success", "fatigue", "deadline_near"}

    def probability_start(self, context: dict[str, Any]) -> float:
        depleted = sigmoid(1.0 * (7.0 - context.get("sleep_hours", 7.0)) + 1.2 * (1.0 - context.get("previous_task_success", 0.0)))
        exploratory_logit = 0.7 + 1.4 * context.get("deadline_near", 0.0) - 0.5 * context.get("ambiguity", 0.0)
        depleted_logit = -1.3 + 0.8 * context.get("deadline_near", 0.0) - 1.1 * context.get("fatigue", 0.0)
        return (1.0 - depleted) * sigmoid(exploratory_logit) + depleted * sigmoid(depleted_logit)


class ThresholdPersonWorld(HiddenWorld):
    name = "threshold_person"
    hidden_drivers = {"social_cost", "public_commitment", "deadline_near"}

    def probability_start(self, context: dict[str, Any]) -> float:
        high_social_cost = 1.0 if context.get("social_cost", 0.0) > 0.7 else 0.0
        return sigmoid(0.9 - 3.0 * high_social_cost + 0.8 * context.get("public_commitment", 0.0) + 1.0 * context.get("deadline_near", 0.0))


class NonstationaryWorld(HiddenWorld):
    name = "nonstationary_person"
    hidden_drivers = {"repeated_failures", "explicit_first_step", "ambiguity"}

    def probability_start(self, context: dict[str, Any]) -> float:
        shifted = 1.0 if context.get("repeated_failures", 0.0) >= 3 else 0.0
        return sigmoid(
            0.2
            - 0.8 * shifted
            - 0.7 * context.get("ambiguity", 0.0)
            + (1.4 if shifted else 0.4) * context.get("explicit_first_step", 0.0)
        )


class ConfoundedWorld(HiddenWorld):
    name = "confounded_world"
    hidden_drivers = {"importance", "deadline_near", "task_size_large"}

    def sample_context(self) -> dict[str, Any]:
        context = super().sample_context()
        context["public_commitment"] = 1.0 if context["importance"] > 0.68 and self.random.random() < 0.82 else 0.0
        return context

    def probability_start(self, context: dict[str, Any]) -> float:
        return sigmoid(
            -0.8
            + 2.1 * context.get("importance", 0.0)
            + 1.0 * context.get("deadline_near", 0.0)
            - 0.7 * context.get("task_size_large", 0.0)
        )


WORLD_TYPES = {
    "habit": HabitPlusOverrideWorld,
    "habit_plus_override": HabitPlusOverrideWorld,
    "two_mode": TwoModePersonWorld,
    "threshold": ThresholdPersonWorld,
    "nonstationary": NonstationaryWorld,
    "confounded": ConfoundedWorld,
}


def make_world(name: str, seed: int = 7, subject_id: str = "synthetic_subject") -> HiddenWorld:
    try:
        cls = WORLD_TYPES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown world {name!r}; available: {sorted(WORLD_TYPES)}") from exc
    return cls(seed=seed, subject_id=subject_id)


def episodes_to_payloads(episodes: list[DecisionEpisode]) -> list[dict[str, Any]]:
    return [asdict(episode) for episode in episodes]
