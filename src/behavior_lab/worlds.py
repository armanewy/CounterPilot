from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import math
import random
from typing import Any

from behavior_lab.core import DecisionEpisode, InterventionTrial, new_id
from behavior_lab.dsl import Formula, FormulaSyntaxError


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(min(value, 50.0), -50.0)))


def _derived_seed(seed: int, namespace: str, index: int) -> int:
    digest = hashlib.sha256(f"{seed}:{namespace}:{index}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


class HiddenWorld:
    name = "base"
    hidden_drivers: set[str] = set()
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __init__(self, seed: int = 7, subject_id: str = "synthetic_subject"):
        self.seed = seed
        self.subject_id = subject_id
        self._event_index = 0
        self._context_index = 0
        # Kept for backward compatibility with callers that inspect `.random`.
        # Ledger-producing methods use per-event RNGs and therefore survive restarts.
        self.random = random.Random(seed)

    def set_event_index(self, value: int) -> None:
        self._event_index = max(0, int(value))

    @property
    def event_index(self) -> int:
        return self._event_index

    def next_event_time(self) -> datetime:
        """Timestamp of the next ledger-producing synthetic event."""
        return self.base_time + timedelta(minutes=30 * (self._event_index + 1))

    def _event_rng(self, kind: str) -> tuple[int, random.Random]:
        self._event_index += 1
        return self._event_index, random.Random(_derived_seed(self.seed, kind, self._event_index))

    def sample_context(self) -> dict[str, Any]:
        self._context_index += 1
        rng = random.Random(_derived_seed(self.seed, "context", self._context_index))
        return self._sample_context_with_rng(rng)

    def sample_context_at(self, index: int, *, namespace: str = "probe") -> dict[str, Any]:
        """Return a deterministic non-mutating context for search/evaluation.

        Ledger-producing events use their own event-index streams.  Research tools
        should not advance mutable world state merely to search candidate contexts,
        so they receive an explicit namespace and zero-based index instead.
        """

        if index < 0:
            raise ValueError("context index may not be negative")
        if not namespace.strip():
            raise ValueError("context namespace must be non-empty")
        rng = random.Random(_derived_seed(self.seed, f"context:{namespace}", index + 1))
        return self._sample_context_with_rng(rng)

    def _sample_context_with_rng(self, rng: random.Random) -> dict[str, Any]:
        sleep_hours = max(3.5, min(9.5, rng.gauss(7.0, 1.1)))
        fatigue = max(0.0, min(1.0, 1.0 - (sleep_hours - 4.0) / 6.0 + rng.uniform(-0.12, 0.12)))
        ambiguity = rng.random()
        duration = rng.choice([15, 25, 45, 60, 90, 120, 180])
        deadline_distance_hours = rng.choice([0.25, 1, 4, 12, 24, 72, 168])
        deadline_near = 1.0 if deadline_distance_hours <= 4 else 0.0
        public_commitment = rng.random() < 0.28
        explicit_first_step = rng.random() < 0.45
        recent_context_switches = rng.randint(0, 18)
        time_of_day = rng.choice(["morning", "afternoon", "evening", "late"])
        task_size = "large" if duration >= 90 else "medium" if duration >= 45 else "small"
        previous_task_success = rng.random() < 0.55
        social_cost = rng.random()
        repeated_failures = rng.randint(0, 5)
        importance = rng.random()
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
        """Synthetic evaluator-only variable recall, not proof of mechanism recovery."""
        if not self.hidden_drivers:
            return 0.0
        try:
            variables = Formula.parse(terms).variables
        except FormulaSyntaxError:
            return 0.0
        matched = len(self.hidden_drivers.intersection(variables))
        return matched / len(self.hidden_drivers)

    def generate_episode(self, context: dict[str, Any] | None = None) -> DecisionEpisode:
        event_index, rng = self._event_rng("episode")
        context = dict(context or self._sample_context_with_rng(rng))
        probability = self.probability_start(context)
        started = rng.random() < probability
        action = "start_now" if started else rng.choice(["prepare_without_starting", "switch_task", "defer"])
        latency = int(rng.expovariate(1 / 260)) if started else int(rng.expovariate(1 / 900) + 600)
        decision_time = self.base_time + timedelta(minutes=30 * event_index)
        return DecisionEpisode.create(
            subject_id=self.subject_id,
            decision_time=decision_time.isoformat(),
            observation_cutoff=(decision_time - timedelta(seconds=1)).isoformat(),
            situation={"type": "start_planned_task", "description": f"synthetic task {event_index}", "world": self.name},
            available_actions=["start_now", "prepare_without_starting", "switch_task", "defer", "abandon"],
            pre_decision_context=context,
            observed_action={"action": action, "latency_seconds": latency},
            later_outcomes={
                "started_within_10_minutes": bool(started and latency <= 600),
                "started_within_2_hours": bool(started or rng.random() < 0.35),
                "completed_within_day": bool(started and rng.random() < 0.55),
            },
            data_provenance={
                "world": self.name,
                "mechanism_hidden_from_researcher": True,
                "random_seed": self.seed,
                "event_index": event_index,
            },
        )

    def generate_dataset(self, episodes: int) -> list[DecisionEpisode]:
        if episodes < 0:
            raise ValueError("episodes may not be negative")
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
        event_index, rng = self._event_rng("trial")
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
        started = rng.random() < probability_start
        latency = int(rng.expovariate(1 / 220)) if started else int(rng.expovariate(1 / 900) + 650)
        recorded_at = self.base_time + timedelta(minutes=30 * event_index)
        assigned_probability = probability if assigned_treatment == treatment else 1.0 - probability
        return InterventionTrial.create(
            subject_id=self.subject_id,
            context_snapshot_id=new_id("c"),
            comparison={"treatment": treatment, "comparator": comparator},
            assignment={
                "method": "randomized_block",
                "assigned_treatment": assigned_treatment,
                "probability": probability,
                "treatment_probability": probability,
                "assigned_probability": assigned_probability,
                "block": {
                    "fatigue_band": "high" if context.get("fatigue", 0.0) > 0.66 else "medium",
                    "task_size": "large" if context.get("task_size_large", 0.0) else "small_or_medium",
                },
            },
            adherence={"treatment_delivered": True, "treatment_seen": True},
            outcomes={
                "started_within_10_minutes": bool(started and latency <= 600),
                "time_to_start_seconds": latency,
                "completed_within_day": bool(started and rng.random() < 0.55),
            },
            measurement_horizons=["10_minutes", "2_hours", "1_day"],
            preregistration_id=preregistration_id,
            recorded_at=recorded_at.isoformat(),
            data_provenance={
                "world": self.name,
                "offline_synthetic_trial": True,
                "context_snapshot": context,
                "intervened_context": intervened,
                "event_index": event_index,
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
        depleted = sigmoid(
            1.0 * (7.0 - context.get("sleep_hours", 7.0))
            + 1.2 * (1.0 - context.get("previous_task_success", 0.0))
        )
        exploratory_logit = 0.7 + 1.4 * context.get("deadline_near", 0.0) - 0.5 * context.get("ambiguity", 0.0)
        depleted_logit = -1.3 + 0.8 * context.get("deadline_near", 0.0) - 1.1 * context.get("fatigue", 0.0)
        return (1.0 - depleted) * sigmoid(exploratory_logit) + depleted * sigmoid(depleted_logit)


class ThresholdPersonWorld(HiddenWorld):
    name = "threshold_person"
    hidden_drivers = {"social_cost", "public_commitment", "deadline_near"}

    def probability_start(self, context: dict[str, Any]) -> float:
        high_social_cost = 1.0 if context.get("social_cost", 0.0) > 0.7 else 0.0
        return sigmoid(
            0.9
            - 3.0 * high_social_cost
            + 0.8 * context.get("public_commitment", 0.0)
            + 1.0 * context.get("deadline_near", 0.0)
        )


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

    def _sample_context_with_rng(self, rng: random.Random) -> dict[str, Any]:
        context = super()._sample_context_with_rng(rng)
        context["public_commitment"] = 1.0 if context["importance"] > 0.68 and rng.random() < 0.82 else 0.0
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
