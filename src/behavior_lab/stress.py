from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from behavior_lab.core import HypothesisSpec, parse_time
from behavior_lab.evaluation import evaluate_model
from behavior_lab.gym import WorldGym
from behavior_lab.models import BaseRateModel, FittedLogisticFormula, LogisticFormulaHypothesis, ModelFoundry
from behavior_lab.temporal import assert_snapshot_is_pre_decision, pre_decision_snapshot
from behavior_lab.worlds import make_world


class StressDatasetMismatch(RuntimeError):
    pass


class LabStressTester:
    """Executable self-audit for chronology, lockbox redaction, and baselines."""

    def run(
        self,
        data_dir: str | Path,
        *,
        episodes: int = 160,
        seed: int = 17,
        world: str = "habit",
        require_exact_dataset: bool = False,
    ) -> dict[str, Any]:
        gym = WorldGym(data_dir, world=make_world(world, seed=seed), campaign_id="stress")
        existing = gym.decision_episodes()
        if not existing:
            gym.seed(episodes)
        elif require_exact_dataset:
            self._validate_dataset(existing, episodes, gym.world.name, seed)
        splits = gym.splits("stress")
        models = ModelFoundry().fit_zoo(splits["training"], splits["development"], gym.target_name)
        dev = [evaluate_model(model, splits["development"], split="development") for model in models]
        dev.sort(key=lambda metric: metric.log_loss)
        base_rate = next(
            (metric for metric in dev if isinstance(next(model for model in models if model.model_id == metric.model_id), BaseRateModel)),
            None,
        )
        hidden_payload = gym.blind_server("stress").evaluate(models[0], split="hidden")
        leakage_ok = self._check_temporal_firewall(gym)
        chronology_ok = self._check_split_chronology(splits)
        language_probe_score = self._formula_language_driver_recall_probe(gym, "stress")
        best_formula = self._best_discovered_formula_model(models, dev)
        best_formula_terms = self._formula_terms(best_formula)
        hidden_driver_recall = gym.world.mechanism_equivalence_score(best_formula_terms)
        intervention_direction_accuracy = self._intervention_direction_accuracy(gym, best_formula)
        best = dev[0]
        result = {
            "world": gym.world.name,
            "episodes": len(gym.decision_episodes()),
            "splits": {name: len(rows) for name, rows in splits.items()},
            "temporal_firewall_ok": leakage_ok,
            "split_chronology_ok": chronology_ok,
            "initial_prospective_empty": len(splits["prospective"]) == 0,
            "hidden_payload_redacted": hidden_payload.get("details", {}).get("redacted") is not None,
            "best_development_model": asdict(best),
            "base_rate_development_model": asdict(base_rate) if base_rate else None,
            "best_beats_base_log_loss": bool(base_rate and best.log_loss <= base_rate.log_loss),
            "best_discovered_formula_hidden_driver_recall": hidden_driver_recall,
            "best_discovered_formula_terms": best_formula_terms,
            "intervention_direction_accuracy": intervention_direction_accuracy,
            "formula_language_driver_recall_probe": language_probe_score,
            "warnings": self._warnings(splits, base_rate is not None and best.log_loss <= base_rate.log_loss),
        }
        # Backward-compatible name, now explicitly documented as variable recall,
        # not full mechanism recovery.
        result["best_formula_mechanism_recall"] = hidden_driver_recall
        return result

    def run_world_matrix(
        self, data_dir: str | Path, *, episodes: int = 140, seed: int = 23
    ) -> list[dict[str, Any]]:
        reports = []
        for world_name in ["habit", "two_mode", "threshold", "nonstationary", "confounded"]:
            reports.append(
                self.run(
                    Path(data_dir) / world_name,
                    episodes=episodes,
                    seed=seed,
                    world=world_name,
                    require_exact_dataset=False,
                )
            )
        return reports

    def _validate_dataset(
        self, episodes: list[dict[str, Any]], expected_count: int, expected_world: str, expected_seed: int
    ) -> None:
        if len(episodes) != expected_count:
            raise StressDatasetMismatch(
                f"Existing stress dataset has {len(episodes)} episodes; expected exactly {expected_count}"
            )
        for episode in episodes:
            provenance = episode.get("data_provenance", {})
            if provenance.get("world") != expected_world or int(provenance.get("random_seed", -1)) != expected_seed:
                raise StressDatasetMismatch("Existing stress dataset world/seed does not match the requested run")

    def _check_temporal_firewall(self, gym: WorldGym) -> bool:
        for episode in gym.decision_episodes()[:20]:
            snapshot = pre_decision_snapshot(episode)
            assert_snapshot_is_pre_decision(snapshot)
            if "observed_action" in snapshot or "later_outcomes" in snapshot or "data_provenance" in snapshot:
                return False
        return True

    def _check_split_chronology(self, splits: dict[str, list[dict[str, Any]]]) -> bool:
        ordered = [splits["training"], splits["development"], splits["hidden"]]
        previous_max = None
        for rows in ordered:
            if not rows:
                continue
            current_min = min(parse_time(row["decision_time"]) for row in rows)
            current_max = max(parse_time(row["decision_time"]) for row in rows)
            if previous_max is not None and current_min < previous_max:
                return False
            previous_max = current_max
        return True

    def _best_discovered_formula_model(self, models: list[Any], metrics: list[Any]) -> Any | None:
        rank = {metric.model_id: index for index, metric in enumerate(metrics)}
        candidates = [
            model
            for model in models
            if isinstance(model, FittedLogisticFormula)
            and getattr(model, "origin", "") in {"symbolic_search", "llm_proposal", "heuristic_generator", "heuristic_mutation"}
        ]
        candidates.sort(key=lambda model: rank.get(model.model_id, len(rank)))
        return candidates[0] if candidates else None

    def _formula_terms(self, model: Any | None) -> list[str]:
        if model is None or not hasattr(model, "formula"):
            return []
        return [term.expression for term in model.formula.terms]

    def _intervention_direction_accuracy(self, gym: WorldGym, model: Any | None) -> float | None:
        if model is None:
            return None
        comparisons = [
            ("explicit_first_step", "generic_task_description"),
            ("visible_commitment", "no_intervention"),
            ("two_minute_countdown", "no_intervention"),
        ]
        correct = 0
        total = 0
        for treatment, comparator in comparisons:
            for _ in range(8):
                context = gym.world.sample_context()
                treatment_context = self._apply_intervention(context, treatment)
                comparator_context = self._apply_intervention(context, comparator)
                true_effect = gym.world.probability_start(treatment_context) - gym.world.probability_start(comparator_context)
                predicted_effect = model.predict_proba(treatment_context) - model.predict_proba(comparator_context)
                total += 1
                if abs(true_effect) < 0.02:
                    correct += 1 if abs(predicted_effect) < 0.05 else 0
                else:
                    correct += 1 if true_effect * predicted_effect > 0 else 0
        return correct / total if total else None

    def _apply_intervention(self, context: dict[str, Any], intervention: str) -> dict[str, Any]:
        updated = dict(context)
        if intervention == "explicit_first_step":
            updated["explicit_first_step"] = 1.0
        elif intervention == "generic_task_description":
            updated["explicit_first_step"] = 0.0
        elif intervention == "visible_commitment":
            updated["public_commitment"] = 1.0
        elif intervention == "two_minute_countdown":
            updated["deadline_near"] = 1.0
        return updated

    def _formula_language_driver_recall_probe(self, gym: WorldGym, campaign_id: str) -> float:
        rows = gym.splits(campaign_id)["training"]
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
            origin="evaluator_probe",
        )
        model = LogisticFormulaHypothesis(spec).fit(rows)
        terms = [term.expression for term in model.formula.terms]
        return gym.world.mechanism_equivalence_score(terms)

    def _warnings(self, splits: dict[str, list[dict[str, Any]]], beats_base: bool) -> list[str]:
        warnings: list[str] = []
        if splits.get("prospective"):
            warnings.append("initial manifest unexpectedly contains prospective cases")
        else:
            warnings.append("prospective evidence requires a freeze followed by newly collected cases")
        if not beats_base:
            warnings.append("no discovered model beat the base-rate baseline on development")
        return warnings
