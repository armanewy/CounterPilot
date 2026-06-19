from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from behavior_lab.core import HypothesisSpec
from behavior_lab.evaluation import evaluate_model
from behavior_lab.gym import WorldGym
from behavior_lab.models import BaseRateModel, LogisticFormulaHypothesis, ModelFoundry
from behavior_lab.temporal import assert_snapshot_is_pre_decision, pre_decision_snapshot
from behavior_lab.worlds import HabitPlusOverrideWorld, make_world


class LabStressTester:
    """Small self-audit suite for the discovery infrastructure.

    This is intentionally code, not just documentation: the lab should be able
    to challenge its own assumptions after each wave. The checks target the
    exact failure modes we care about: temporal leakage, baselines beating fancy
    hypotheses, hidden labels being redacted, and formula discovery recovering
    at least part of a known synthetic mechanism.
    """

    def run(self, data_dir: str | Path, *, episodes: int = 160, seed: int = 17) -> dict[str, Any]:
        gym = WorldGym(data_dir, world=HabitPlusOverrideWorld(seed=seed))
        if not gym.decision_episodes():
            gym.seed(episodes)
        splits = gym.splits()
        models = ModelFoundry().fit_zoo(splits["training"], splits["development"], gym.target_name)
        dev = [evaluate_model(model, splits["development"], split="development") for model in models]
        dev.sort(key=lambda metric: metric.log_loss)
        base_rate = next((metric for metric in dev if metric.complexity == 1), None)
        hidden_payload = gym.blind_server().evaluate(models[0], split="hidden")
        leakage_ok = self._check_temporal_firewall(gym)
        formula_score = self._formula_driver_recall(gym)
        best = dev[0]
        return {
            "episodes": len(gym.decision_episodes()),
            "splits": {name: len(rows) for name, rows in splits.items()},
            "temporal_firewall_ok": leakage_ok,
            "hidden_payload_redacted": hidden_payload.get("details", {}).get("redacted") is not None,
            "best_development_model": asdict(best),
            "base_rate_development_model": asdict(base_rate) if base_rate else None,
            "best_beats_base_log_loss": bool(base_rate and best.log_loss <= base_rate.log_loss),
            "formula_driver_recall": formula_score,
            "warnings": self._warnings(splits, formula_score, base_rate is not None and best.log_loss <= base_rate.log_loss),
        }

    def run_world_matrix(self, data_dir: str | Path, *, episodes: int = 140, seed: int = 23) -> list[dict[str, Any]]:
        reports = []
        for world_name in ["habit", "two_mode", "threshold", "nonstationary", "confounded"]:
            gym = WorldGym(Path(data_dir) / world_name, world=make_world(world_name, seed=seed))
            if not gym.decision_episodes():
                gym.seed(episodes)
            splits = gym.splits()
            models = ModelFoundry().fit_zoo(splits["training"], splits["development"], gym.target_name)
            metrics = [evaluate_model(model, splits["development"], split="development") for model in models]
            metrics.sort(key=lambda item: item.log_loss)
            reports.append(
                {
                    "world": gym.world.name,
                    "splits": {name: len(rows) for name, rows in splits.items()},
                    "best_model_id": metrics[0].model_id,
                    "best_log_loss": metrics[0].log_loss,
                    "best_complexity": metrics[0].complexity,
                    "mechanism_recall_of_best_formula_terms": self._formula_driver_recall(gym),
                }
            )
        return reports

    def _check_temporal_firewall(self, gym: WorldGym) -> bool:
        for episode in gym.decision_episodes()[:20]:
            snapshot = pre_decision_snapshot(episode)
            assert_snapshot_is_pre_decision(snapshot)
            if "observed_action" in snapshot or "later_outcomes" in snapshot:
                return False
        return True

    def _formula_driver_recall(self, gym: WorldGym) -> float:
        rows = gym.splits()["training"]
        spec = HypothesisSpec.formula(
            "stress_known_driver_probe",
            gym.target_name,
            [
                "explicit_first_step",
                "indicator(ambiguity > 0.6)",
                "explicit_first_step * indicator(ambiguity > 0.6)",
                "fatigue",
                "deadline_near",
                "public_commitment",
                "recent_context_switches",
            ],
        )
        model = LogisticFormulaHypothesis(spec).fit(rows)
        terms = [term.expression for term in model.formula.terms]
        return gym.world.mechanism_equivalence_score(terms)

    def _warnings(self, splits: dict[str, list[dict[str, Any]]], formula_score: float, beats_base: bool) -> list[str]:
        warnings: list[str] = []
        if len(splits.get("prospective", [])) == 0:
            warnings.append("prospective split is empty; freeze-and-forward claims are not meaningful yet")
        if formula_score < 0.5:
            warnings.append("formula probe recovered less than half of known synthetic drivers")
        if not beats_base:
            warnings.append("no discovered model beat the base-rate baseline on development")
        return warnings
