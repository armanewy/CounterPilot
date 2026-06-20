from __future__ import annotations

from dataclasses import asdict
from statistics import mean, pstdev
from typing import Any, Callable, Protocol

from behavior_lab.core import HypothesisSpec, new_id, stable_hash
from behavior_lab.dsl import Formula, MAX_TERMS
from behavior_lab.evaluation import residuals
from behavior_lab.gym import TARGET, WorldGym
from behavior_lab.registry import ModelRegistry
from behavior_lab.research_api import ResearchAPI
from behavior_lab.temporal import feature_catalog


class GeneratorProtocol(Protocol):
    def seed_hypotheses(
        self, rows: list[dict[str, Any]], target_name: str = TARGET
    ) -> list[HypothesisSpec]: ...

    def mutate_from_residuals(
        self,
        parent: HypothesisSpec,
        residual_rows: list[dict[str, Any]],
        target_name: str = TARGET,
    ) -> HypothesisSpec: ...


class HypothesisGenerator:
    """Deterministic generic hypothesis generator used as an offline baseline."""

    def seed_hypotheses(self, rows: list[dict[str, Any]], target_name: str = TARGET) -> list[HypothesisSpec]:
        ranked = self._rank_variables(rows)
        if not ranked:
            terms: list[str] = []
            return [
                HypothesisSpec.formula(
                    self._content_id("intercept", terms),
                    target_name,
                    terms,
                    origin="heuristic_generator",
                    falsification_conditions=["does not beat the base-rate baseline"],
                )
            ]
        linear_terms = ranked[:5]
        specs = [
            HypothesisSpec.formula(
                self._content_id("linear", linear_terms),
                target_name,
                linear_terms,
                origin="heuristic_generator",
                falsification_conditions=["top generic linear terms do not improve development log loss"],
            )
        ]
        first = ranked[0]
        threshold = self._median(rows, first)
        threshold_terms = [f"indicator({first} > {threshold:.6g})"] + ranked[1:3]
        specs.append(
            HypothesisSpec.formula(
                self._content_id("threshold", threshold_terms),
                target_name,
                threshold_terms,
                origin="heuristic_generator",
                falsification_conditions=["threshold is unstable or fails on later chronological cases"],
            )
        )
        if len(ranked) >= 2:
            interaction_terms = [ranked[0], ranked[1], f"{ranked[0]} * {ranked[1]}"] + ranked[2:4]
            specs.append(
                HypothesisSpec.formula(
                    self._content_id("interaction", interaction_terms),
                    target_name,
                    interaction_terms,
                    origin="heuristic_generator",
                    falsification_conditions=["the interaction does not improve development generalization"],
                )
            )
        return specs

    def _content_id(self, label: str, terms: list[str], parent_ids: list[str] | None = None) -> str:
        content = {"label": label, "terms": terms, "parent_ids": parent_ids or []}
        return f"h_{label}_{stable_hash(content)[:10]}"

    def mutate_from_residuals(
        self,
        parent: HypothesisSpec,
        residual_rows: list[dict[str, Any]],
        target_name: str = TARGET,
    ) -> HypothesisSpec:
        existing_terms = list(parent.structure.get("terms", []))
        candidates = self._residual_terms(residual_rows)
        parent_ids = [parent.hypothesis_id]
        for term in candidates:
            if term not in existing_terms and len(existing_terms) < MAX_TERMS:
                mutated_terms = existing_terms + [term]
                return HypothesisSpec.formula(
                    self._content_id("mutation", mutated_terms, parent_ids),
                    target_name,
                    mutated_terms,
                    parent_ids=parent_ids,
                    origin="heuristic_mutation",
                    falsification_conditions=[f"residual term {term!r} fails on a later campaign"],
                )
        return HypothesisSpec.formula(
            self._content_id("mutation", existing_terms, parent_ids),
            target_name,
            existing_terms,
            parent_ids=parent_ids,
            origin="heuristic_mutation",
            falsification_conditions=["no admissible residual mutation improved chronological generalization"],
        )

    def _rank_variables(self, rows: list[dict[str, Any]]) -> list[str]:
        names = feature_catalog(rows)
        scored: list[tuple[float, str]] = []
        for name in names:
            positives = [float(row["features"].get(name, 0.0)) for row in rows if int(row["target"]) == 1]
            negatives = [float(row["features"].get(name, 0.0)) for row in rows if int(row["target"]) == 0]
            if not positives or not negatives:
                score = 0.0
            else:
                values = positives + negatives
                scale = pstdev(values) if len(values) > 1 else 1.0
                score = abs(mean(positives) - mean(negatives)) / max(scale, 1e-9)
            scored.append((score, name))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [name for _, name in scored]

    def _median(self, rows: list[dict[str, Any]], name: str) -> float:
        values = sorted(float(row["features"].get(name, 0.0)) for row in rows)
        return values[len(values) // 2] if values else 0.0

    def _residual_terms(self, residual_rows: list[dict[str, Any]]) -> list[str]:
        if not residual_rows:
            return []
        numeric_names = sorted(
            {
                name
                for row in residual_rows
                for name, value in row.get("features", {}).items()
                if name != "bias" and isinstance(value, (int, float, bool))
            }
        )
        scored: list[tuple[float, str]] = []
        for name in numeric_names:
            xs = [float(row["features"].get(name, 0.0)) for row in residual_rows]
            ys = [float(row["target"]) - float(row["prediction"]) for row in residual_rows]
            if len(xs) < 2 or pstdev(xs) <= 1e-9 or pstdev(ys) <= 1e-9:
                score = 0.0
            else:
                x_mean, y_mean = mean(xs), mean(ys)
                covariance = mean((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True))
                score = abs(covariance / (pstdev(xs) * pstdev(ys)))
            scored.append((score, name))
        scored.sort(key=lambda item: (-item[0], item[1]))
        terms: list[str] = []
        for _, name in scored:
            values = sorted(float(row["features"].get(name, 0.0)) for row in residual_rows)
            median_value = values[len(values) // 2] if values else 0.0
            terms.extend([name, f"indicator({name} > {median_value:.6g})"])
        if len(scored) >= 2:
            terms.insert(0, f"{scored[0][1]} * {scored[1][1]}")
        return terms


class LLMHypothesisGenerator:
    """Validated provider seam for external hypothesis proposals."""

    def __init__(self, proposer: Callable[[dict[str, Any]], list[dict[str, Any]]]):
        self.proposer = proposer

    def propose(self, api: Any, *, max_hypotheses: int = 5) -> list[HypothesisSpec]:
        if max_hypotheses <= 0 or max_hypotheses > 20:
            raise ValueError("max_hypotheses must be between 1 and 20")
        variables = set(api.list_variables())
        request = {
            "schema": api.inspect_schema(),
            "target": api.describe_target(),
            "variables": sorted(variables),
            "rules": [
                "Return small executable formulas only.",
                "Use only listed variables.",
                "Include assumptions and falsification conditions.",
                "Do not claim causality from observational association.",
            ],
            "max_hypotheses": max_hypotheses,
        }
        raw_candidates = self.proposer(request)
        if not isinstance(raw_candidates, list):
            raise ValueError("LLM proposer must return a list of hypothesis objects")
        specs: list[HypothesisSpec] = []
        seen_ids: set[str] = set()
        for candidate in raw_candidates:
            if not isinstance(candidate, dict):
                raise ValueError("Each LLM hypothesis must be an object")
            raw_terms = candidate.get("terms", [])
            if not isinstance(raw_terms, list):
                raise ValueError("LLM hypothesis terms must be a list of strings")
            terms = [str(term).strip() for term in raw_terms if str(term).strip()]
            if not terms:
                continue
            formula = Formula.parse(terms)
            unknown = set(formula.variables) - variables
            if unknown:
                raise ValueError(f"LLM hypothesis used unknown variables: {sorted(unknown)}")
            hypothesis_id = str(candidate.get("hypothesis_id") or new_id("h_llm"))
            if hypothesis_id in seen_ids:
                raise ValueError(f"LLM returned duplicate hypothesis ID: {hypothesis_id}")
            seen_ids.add(hypothesis_id)
            specs.append(
                HypothesisSpec.formula(
                    hypothesis_id=hypothesis_id,
                    target_name=api.gym.target_name,
                    terms=terms,
                    origin="llm_proposal",
                    assumptions=self._string_list(
                        candidate.get("assumptions"),
                        field_name="assumptions",
                        fallback=["LLM formula passed DSL and variable validation"],
                    ),
                    falsification_conditions=self._string_list(
                        candidate.get("falsification_conditions"),
                        field_name="falsification_conditions",
                        fallback=["does not beat baseline models on development and a later campaign"],
                    ),
                )
            )
            if len(specs) >= max_hypotheses:
                break
        return specs

    def _string_list(self, value: Any, *, field_name: str, fallback: list[str]) -> list[str]:
        if value is None:
            return list(fallback)
        if not isinstance(value, list):
            raise ValueError(f"LLM hypothesis {field_name} must be a list of strings")
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return cleaned or list(fallback)


class DiscoveryLoop:
    """Closed-loop synthetic researcher using fresh campaign manifests."""

    def __init__(self, gym: WorldGym, generator: HypothesisGenerator | LLMHypothesisGenerator | None = None):
        self.gym = gym
        self.registry = ModelRegistry(gym.ledger)
        self.generator = generator or HypothesisGenerator()

    def run(
        self,
        iterations: int = 3,
        offline_trials_per_iteration: int = 8,
        prospective_episodes: int = 40,
    ) -> dict[str, Any]:
        if iterations <= 0 or offline_trials_per_iteration <= 0 or prospective_episodes < 0:
            raise ValueError("iterations/trials must be positive and prospective_episodes nonnegative")
        run_id = new_id("loop")
        report: dict[str, Any] = {"run_id": run_id, "iterations": []}
        active_specs: list[HypothesisSpec] = []

        for iteration in range(iterations):
            campaign_id = f"{run_id}_iter_{iteration + 1}"
            self.gym.create_campaign(campaign_id)
            api = ResearchAPI(self.gym, campaign_id=campaign_id)
            splits = self.gym.splits(campaign_id)
            training = splits["training"]
            development = splits["development"]
            if not active_specs:
                if isinstance(self.generator, LLMHypothesisGenerator):
                    active_specs = self.generator.propose(api)
                else:
                    active_specs = self.generator.seed_hypotheses(training, self.gym.target_name)

            fitted_models: list[Any] = []
            spec_by_model: dict[str, HypothesisSpec] = {}
            for spec in active_specs:
                api.submit_hypothesis(spec)
                fit = api.fit_hypothesis(spec.hypothesis_id)
                model = api.models[fit["model_id"]]
                fitted_models.append(model)
                spec_by_model[model.model_id] = spec
            zoo = api.fit_model_zoo()
            candidates = fitted_models + zoo
            scored = [(model, api.evaluate_hypothesis(model.model_id, "development")) for model in candidates]
            scored.sort(key=lambda item: item[1]["log_loss"])
            best_model, best_metrics = scored[0]
            best_spec = spec_by_model.get(best_model.model_id)
            if best_spec is not None and best_metrics["lift_over_base_log_loss"] > 0:
                self.registry.promote_hypothesis(
                    best_spec.hypothesis_id,
                    best_model.model_id,
                    "best development log loss this campaign",
                    campaign_id=campaign_id,
                )
            for model, metrics in scored[-2:]:
                retired_spec = spec_by_model.get(model.model_id)
                if retired_spec is None:
                    continue
                self.registry.retire_hypothesis(
                    retired_spec.hypothesis_id,
                    "dominated on development in this campaign",
                    {"log_loss": metrics["log_loss"], "best_log_loss": best_metrics["log_loss"]},
                    campaign_id=campaign_id,
                )
            residual_summary = residuals(best_model, development, limit=10)
            if best_spec and not isinstance(self.generator, LLMHypothesisGenerator):
                active_specs = [
                    best_spec,
                    self.generator.mutate_from_residuals(best_spec, residual_summary, self.gym.target_name),
                ]
            elif isinstance(self.generator, LLMHypothesisGenerator):
                active_specs = self.generator.propose(api)
            else:
                active_specs = self.generator.seed_hypotheses(training, self.gym.target_name)

            # Search disagreements among the strongest development contenders,
            # not merely whichever models happened to be constructed first.
            proposal = api.propose_experiment(
                [model.model_id for model, _ in scored[: min(6, len(scored))]],
                search_round=iteration,
            )
            experiment_summary = api.run_offline_experiment(proposal, trials=offline_trials_per_iteration)
            report["iterations"].append(
                {
                    "iteration": iteration + 1,
                    "campaign_id": campaign_id,
                    "training_cases": len(training),
                    "development_cases": len(development),
                    "hidden_cases_not_queried": len(splits["hidden"]),
                    "best_model_id": best_model.model_id,
                    "best_log_loss": best_metrics["log_loss"],
                    "best_lift_over_base": best_metrics["lift_over_base_log_loss"],
                    "proposal": asdict(proposal),
                    "experiment": experiment_summary,
                }
            )

        final_campaign = f"{run_id}_final"
        self.gym.create_campaign(final_campaign)
        final_api = ResearchAPI(self.gym, campaign_id=final_campaign, hidden_budget=1, prospective_budget=1)
        final_models: list[Any] = []
        for spec in active_specs:
            final_api.submit_hypothesis(spec)
            fit = final_api.fit_hypothesis(spec.hypothesis_id)
            final_models.append(final_api.models[fit["model_id"]])
        final_models.extend(final_api.fit_model_zoo())
        development_scores = [
            (model, final_api.evaluate_hypothesis(model.model_id, "development")) for model in final_models
        ]
        development_scores.sort(key=lambda item: item[1]["log_loss"])
        frozen_model, development_winner = development_scores[0]
        freeze = final_api.freeze_candidate(
            frozen_model.model_id,
            "selected and frozen on development before hidden/prospective evaluation",
        )
        hidden_result = final_api.evaluate_hypothesis(frozen_model.model_id, "hidden")
        if prospective_episodes:
            self.gym.seed(prospective_episodes)
            self.gym.ensure_split_manifest(campaign_id=final_campaign)
            prospective_result = final_api.submit_frozen_candidate(frozen_model.model_id)
        else:
            prospective_result = None
        report["final"] = {
            "campaign_id": final_campaign,
            "selected_model_id": frozen_model.model_id,
            "selected_model_origin": getattr(frozen_model, "origin", "unknown"),
            "freeze_id": freeze["payload"]["freeze_id"],
            "artifact_hash": freeze["payload"]["artifact_hash"],
            "development_result": development_winner,
            "hidden_result": hidden_result,
            "prospective_result": prospective_result,
            "prospective_cases_generated_after_freeze": prospective_episodes,
            "hidden_submissions": 1,
            "prospective_submissions": 1 if prospective_result else 0,
        }
        return report
