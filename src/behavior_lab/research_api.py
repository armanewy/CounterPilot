from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
import copy
from typing import Any

from behavior_lab.core import HypothesisSpec, parse_time, stable_hash
from behavior_lab.dsl import Formula
from behavior_lab.evaluation import counterexamples, evaluate_model, paired_compare, residuals
from behavior_lab.experiments import DisagreementFinder, ExperimentProposal, ExperimentScheduler
from behavior_lab.gym import BlindEvaluationServer, EmptyEvaluationSplit, TARGET, WorldGym
from behavior_lab.models import (
    LogisticFormulaHypothesis,
    ModelFoundry,
    model_from_artifact,
    model_to_artifact,
)
from behavior_lab.registry import EvaluationBudgetError, ModelRegistry, RegistryConflictError
from behavior_lab.temporal import feature_catalog


EvaluationBudgetExceeded = EvaluationBudgetError


class ResearchAPI:
    """Typed researcher/LLM-facing facade with persistent scientific lockboxes.

    This prevents accidental leakage and repeated aggregate probing by normal
    clients.  It is not a hostile-code sandbox because callers share a Python
    process and can still bypass this facade if given direct filesystem access.
    """

    def __init__(
        self,
        gym: WorldGym,
        *,
        campaign_id: str = "default",
        hidden_budget: int = 1,
        prospective_budget: int = 1,
    ):
        if not campaign_id.strip():
            raise ValueError("campaign_id must be non-empty")
        if hidden_budget < 0 or prospective_budget < 0:
            raise ValueError("evaluation budgets may not be negative")
        if hidden_budget > 1:
            raise ValueError(
                "hidden_budget may be only 0 or 1 because hidden cases are a one-shot lockbox"
            )
        self.gym = gym
        self.registry = ModelRegistry(gym.ledger)
        self.models: dict[str, Any] = {}
        self.hypotheses: dict[str, HypothesisSpec] = {}
        self.campaign_id = campaign_id
        self.hidden_budget = hidden_budget
        self.prospective_budget = prospective_budget
        self.artifact_load_errors: list[dict[str, str]] = []
        self._load_registry_state()
        self.gym.ensure_split_manifest(campaign_id=campaign_id)

    def _splits(self) -> dict[str, list[dict[str, Any]]]:
        return self.gym.splits(self.campaign_id)

    def inspect_schema(self) -> dict[str, Any]:
        return {
            "record_types": [
                "world_configuration",
                "campaign_start",
                "decision_episode",
                "intervention_trial",
                "hypothesis",
                "model_fit",
                "evaluation",
                "evaluation_budget_use",
                "experiment_preregistration",
                "intervention_assignment",
                "split_assignment",
                "research_run_start",
                "research_run_end",
                "frozen_candidate",
                "model_obituary",
            ],
            "campaign_id": self.campaign_id,
            "target": {"name": self.gym.target_name, "type": "binary"},
            "splits": {name: len(rows) for name, rows in self._splits().items()},
            "staging_cases": len(self.gym.staging_rows(self.campaign_id)),
            "prospective_semantics": "cases first recorded after a hashed model artifact was frozen",
            "lockbox_note": "Logical API boundary only; isolate untrusted LLM/model code out of process.",
            "artifact_load_errors": list(self.artifact_load_errors),
        }

    def list_variables(self) -> list[str]:
        return feature_catalog(self._splits()["training"])

    def describe_target(self, target_id: str = TARGET) -> dict[str, Any]:
        if target_id != self.gym.target_name:
            raise KeyError(f"Unknown target {target_id!r}; active target is {self.gym.target_name!r}")
        rows = self._splits()["training"]
        positives = sum(int(row["target"]) for row in rows)
        return {
            "target_id": target_id,
            "definition": "Whether the intended task began within ten minutes.",
            "training_cases": len(rows),
            "training_base_rate": positives / len(rows) if rows else 0.0,
        }

    def query_training_data(self, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is not None and limit < 0:
            raise ValueError("limit may not be negative")
        return self.gym.blind_server(self.campaign_id).query_training_data(limit=limit)

    def inspect_model_registry(self) -> dict[str, Any]:
        return self.registry.inspect_model_registry(self.campaign_id)

    def inspect_model_lineage(self, model_id: str | None = None) -> dict[str, Any]:
        graph = self.registry.lineage_graph()
        if model_id is None:
            return graph
        return {
            "nodes": {
                key: value
                for key, value in graph["nodes"].items()
                if key == model_id or model_id in str(value)
            },
            "edges": [edge for edge in graph["edges"] if model_id in {edge["from"], edge["to"]}],
        }

    def submit_hypothesis(self, hypothesis_spec: HypothesisSpec) -> dict[str, Any]:
        # Persist first.  A conflicting reused ID must not poison the in-memory
        # cache with content the immutable registry rejected.
        record = self.registry.submit_hypothesis(hypothesis_spec)
        self.hypotheses[hypothesis_spec.hypothesis_id] = hypothesis_spec
        return record

    def fit_hypothesis(self, hypothesis_id: str) -> dict[str, Any]:
        spec = self.hypotheses.get(hypothesis_id)
        if spec is None:
            payload = self.gym.ledger.latest_by_payload_key("hypothesis", "hypothesis_id", hypothesis_id)
            if payload is None:
                raise KeyError(f"Unknown hypothesis: {hypothesis_id}")
            spec = HypothesisSpec(**payload)
            self.hypotheses[hypothesis_id] = spec
        if spec.target.get("name") != self.gym.target_name:
            raise ValueError(
                f"Hypothesis target {spec.target.get('name')!r} does not match active target "
                f"{self.gym.target_name!r}"
            )
        if spec.family != "logistic_formula":
            raise ValueError(f"ResearchAPI currently fits only logistic_formula hypotheses, not {spec.family!r}")
        raw_terms = spec.structure.get("terms", [])
        if not isinstance(raw_terms, list) or any(not isinstance(term, str) for term in raw_terms):
            raise ValueError("Hypothesis formula terms must be a list of strings")
        formula = Formula.parse(list(raw_terms))
        unknown = formula.variables - set(self.list_variables())
        if unknown:
            raise ValueError(f"Hypothesis uses variables absent from the campaign training data: {sorted(unknown)}")
        training_rows = self._splits()["training"]
        model = LogisticFormulaHypothesis(spec).fit(training_rows)
        self.models[model.model_id] = model
        artifact = model_to_artifact(model, training_rows)
        self.registry.record_fit(
            model,
            spec.hypothesis_id,
            "training",
            len(training_rows),
            artifact=artifact,
            campaign_id=self.campaign_id,
        )
        return {
            "model_id": model.model_id,
            "hypothesis_id": hypothesis_id,
            "parameters": model.parameters,
            "campaign_id": self.campaign_id,
            "artifact_hash": artifact["artifact_hash"],
            "training_snapshot_hash": artifact["training_snapshot_hash"],
        }

    def evaluate_hypothesis(self, model_id: str, split: str = "development") -> dict[str, Any]:
        if split not in {"training", "development", "hidden"}:
            if split == "prospective":
                raise PermissionError(
                    "Freeze one candidate, collect future cases, then call submit_frozen_candidate"
                )
            raise ValueError(f"Unknown or inaccessible split: {split}")
        model = self._model(model_id)
        rows = self._splits()[split]
        reservation = None
        freeze = None
        if split == "hidden":
            if not rows:
                raise EmptyEvaluationSplit("Cannot spend the hidden budget on an empty split")
            freeze = self.registry.frozen_candidate(model_id, self.campaign_id)
            if freeze is None:
                raise PermissionError(
                    "Select and freeze the exact candidate artifact before opening the hidden lockbox"
                )
            fit = self.registry.latest_fit(model_id, self.campaign_id)
            if fit is None:
                raise RegistryConflictError("Frozen model no longer has a persisted fit")
            artifact = fit.get("artifact", {})
            current_artifact = model_to_artifact(model, self._splits()["training"])
            if (
                artifact.get("artifact_hash") != freeze.get("artifact_hash")
                or current_artifact.get("artifact_hash") != freeze.get("artifact_hash")
            ):
                raise RegistryConflictError(
                    "Hidden submission model does not match the frozen executable artifact"
                )
            scope_id = f"hidden:{self.gym.split_snapshot_hash('hidden', self.campaign_id)}"
            reservation = self.registry.reserve_evaluation_budget(
                campaign_id=self.campaign_id,
                model_id=model_id,
                split=split,
                scope_id=scope_id,
                case_ids=[str(row["case_id"]) for row in rows],
                limit=self.hidden_budget,
                artifact_hash=str(freeze["artifact_hash"]),
                freeze_id=str(freeze["freeze_id"]),
            )
        result = self.gym.blind_server(self.campaign_id).evaluate(model, split=split)
        if freeze is not None:
            result["freeze_id"] = freeze["freeze_id"]
            result["artifact_hash"] = freeze["artifact_hash"]
        self.registry.record_evaluation_from_payload(
            result,
            campaign_id=self.campaign_id,
            budget_use_id=reservation["payload"]["budget_use_id"] if reservation else None,
        )
        return result

    def compare_models(self, model_a: str, model_b: str, split: str = "development") -> dict[str, Any]:
        if split not in {"training", "development"}:
            raise PermissionError(
                "Pairwise comparison is restricted to training/development; lockboxes expose one aggregate submission"
            )
        return paired_compare(self._model(model_a), self._model(model_b), self._splits()[split])

    def inspect_residuals(self, model_id: str, limit: int = 10) -> list[dict[str, Any]]:
        if limit < 0:
            raise ValueError("limit may not be negative")
        return residuals(self._model(model_id), self._splits()["development"], limit=limit)

    def inspect_counterexamples(self, model_a: str, model_b: str, limit: int = 10) -> list[dict[str, Any]]:
        if limit < 0:
            raise ValueError("limit may not be negative")
        return counterexamples(
            self._model(model_a), self._model(model_b), self._splits()["development"], limit=limit
        )

    def propose_experiment(
        self,
        model_ids: list[str] | None = None,
        *,
        search_round: int = 0,
        candidate_count: int = 64,
    ) -> ExperimentProposal:
        if search_round < 0:
            raise ValueError("search_round may not be negative")
        if candidate_count <= 0 or candidate_count > 2_000:
            raise ValueError("candidate_count must be between 1 and 2000")
        if model_ids is None:
            if not self.models:
                self.fit_model_zoo()
            model_ids = list(self.models)[:6]
        if not model_ids:
            raise ValueError("At least one model is required for experiment proposal")
        models = [self._model(model_id) for model_id in model_ids]

        # Search a deterministic but round-specific context pool without mutating
        # the event stream.  The previous implementation repeatedly searched the
        # same first 20 contexts after every restart, silently narrowing discovery.
        artifact_signatures: list[str] = []
        for model_id in model_ids:
            fit = self.registry.latest_fit(model_id, self.campaign_id)
            artifact_signatures.append(
                str((fit or {}).get("artifact", {}).get("artifact_hash") or model_id)
            )
        namespace = stable_hash(
            {
                "campaign_id": self.campaign_id,
                "model_artifacts": sorted(artifact_signatures),
                "search_round": search_round,
            }
        )[:24]
        contexts = [
            self.gym.world.sample_context_at(index, namespace=namespace)
            for index in range(candidate_count)
        ]
        return DisagreementFinder().propose(models, contexts)

    def simulate_experiment(self, proposal: ExperimentProposal, trials: int = 12) -> list[dict[str, Any]]:
        if trials <= 0:
            raise ValueError("trials must be positive")
        simulated_world = copy.deepcopy(self.gym.world)
        simulated = []
        for index in range(trials):
            assigned = proposal.treatment if index % 2 == 0 else proposal.comparator
            trial = simulated_world.run_intervention_trial(
                proposal.context,
                proposal.treatment,
                proposal.comparator,
                assigned,
                0.5,
            )
            simulated.append(asdict(trial))
        return simulated

    def run_offline_experiment(self, proposal: ExperimentProposal, trials: int = 12) -> dict[str, Any]:
        if self.gym.latest_freeze(self.campaign_id) is not None:
            raise PermissionError(
                "This campaign is frozen. Exploratory offline experiments would contaminate its "
                "prospective block; start a new campaign instead."
            )
        if trials <= 0:
            raise ValueError("trials must be positive")
        scheduler = ExperimentScheduler(
            self.gym.ledger,
            seed=int(stable_hash(proposal.experiment_id)[:12], 16) % (2**31),
        )
        preregistration_id = scheduler.preregister(
            question="Synthetic experiment proposed through ResearchAPI.",
            treatment=proposal.treatment,
            comparator=proposal.comparator,
            target=self.gym.target_name,
            population="synthetic world gym contexts",
            planned_trials=trials,
            stopping_rule=f"Stop after exactly {trials} synthetic assignments.",
            analysis_plan="Estimate randomized intention-to-treat difference in binary means.",
            approval_required=False,
        )
        for _ in range(trials):
            assignment = scheduler.assign_intervention(
                proposal.context,
                treatment=proposal.treatment,
                comparator=proposal.comparator,
                probability=0.5,
                preregistration_id=preregistration_id,
                assigned_at=(self.gym.world.next_event_time() - timedelta(seconds=1)).isoformat(),
            )
            assigned = assignment["assignment"]["assigned_treatment"]
            synthetic_trial = self.gym.world.run_intervention_trial(
                proposal.context,
                proposal.treatment,
                proposal.comparator,
                assigned,
                0.5,
                preregistration_id=preregistration_id,
            )
            scheduler.record_trial_outcome(
                assignment,
                synthetic_trial.outcomes,
                adherence=synthetic_trial.adherence,
                measurement_horizons=synthetic_trial.measurement_horizons,
                subject_id=synthetic_trial.subject_id,
                recorded_at=synthetic_trial.recorded_at,
                data_provenance=synthetic_trial.data_provenance,
            )
        self.gym.ledger.verify_hash_chain()
        effect = scheduler.estimate_treatment_effect(
            treatment=proposal.treatment,
            comparator=proposal.comparator,
            outcome_name=self.gym.target_name,
            preregistration_id=preregistration_id,
        )
        staging_count = len(self.gym.staging_rows(self.campaign_id))
        return {
            "preregistration_id": preregistration_id,
            "trials_appended": trials,
            "ledger_valid": True,
            "effect_estimate": effect,
            "campaign_id": self.campaign_id,
            "staging_cases": staging_count,
            "next_step": "create a new campaign to incorporate staging observations chronologically",
        }

    def freeze_candidate(
        self,
        model_id: str,
        reason: str = "selected on development before future collection",
    ) -> dict[str, Any]:
        self._model(model_id)
        # Classify every observation that already exists as training/dev/hidden or
        # staging before creating the temporal cut. Recompute the cut after an
        # unassigned-observation race instead of freezing an older snapshot.
        for _ in range(3):
            self.gym.ensure_split_manifest(campaign_id=self.campaign_id)
            current_rows = self.gym.rows()
            if not current_rows:
                raise RegistryConflictError("Cannot freeze without observed cases")
            data_cutoff_time = max(
                (str(row["decision_time"]) for row in current_rows),
                key=parse_time,
            )
            split_hash = stable_hash(
                {
                    split: [str(row["case_id"]) for row in self._splits()[split]]
                    for split in ("training", "development", "hidden")
                }
            )
            try:
                return self.registry.freeze_candidate(
                    model_id,
                    "prospective",
                    reason,
                    campaign_id=self.campaign_id,
                    split_snapshot_hash=split_hash,
                    data_cutoff_time=data_cutoff_time,
                )
            except RegistryConflictError as exc:
                if "Unassigned observations" not in str(exc):
                    raise
        raise RegistryConflictError("Could not materialize a stable split snapshot before freezing")

    def submit_frozen_candidate(self, model_id: str) -> dict[str, Any]:
        model = self._model(model_id)
        freeze = self.registry.frozen_candidate(model_id, self.campaign_id)
        if freeze is None:
            raise PermissionError("Candidate must be frozen before prospective observations are collected")
        fit = self.registry.latest_fit(model_id, self.campaign_id)
        if fit is None:
            raise RegistryConflictError("Frozen model no longer has a persisted fit")
        artifact = fit.get("artifact", {})
        if artifact.get("artifact_hash") != freeze.get("artifact_hash"):
            raise RegistryConflictError("Frozen artifact hash does not match the currently loaded model fit")
        current_artifact = model_to_artifact(model, self._splits()["training"])
        if current_artifact.get("artifact_hash") != freeze.get("artifact_hash"):
            raise RegistryConflictError("Loaded model or training snapshot changed after freeze")

        rows = self.gym.prospective_rows_for_freeze(str(freeze["freeze_id"]), self.campaign_id)
        if not rows:
            raise EmptyEvaluationSplit(
                "No post-freeze prospective cases exist. Collect new observations before evaluation."
            )
        scope_id = f"prospective:{freeze['freeze_id']}"
        reservation = self.registry.reserve_evaluation_budget(
            campaign_id=self.campaign_id,
            model_id=model_id,
            split="prospective",
            scope_id=scope_id,
            case_ids=[str(row["case_id"]) for row in rows],
            limit=self.prospective_budget,
            artifact_hash=str(freeze["artifact_hash"]),
            freeze_id=str(freeze["freeze_id"]),
        )
        # Evaluate exactly the freeze-bound rows rather than any unrelated future
        # block that may exist in the same ledger.
        server = BlindEvaluationServer(
            {"training": [], "development": [], "hidden": [], "prospective": rows},
            self.gym.target_name,
            {model_id},
        )
        result = server.evaluate(model, split="prospective")
        result["freeze_id"] = freeze["freeze_id"]
        result["artifact_hash"] = freeze["artifact_hash"]
        self.registry.record_evaluation_from_payload(
            result,
            campaign_id=self.campaign_id,
            budget_use_id=reservation["payload"]["budget_use_id"],
        )
        return result

    def fit_model_zoo(self) -> list[Any]:
        splits = self._splits()
        models = ModelFoundry().fit_zoo(splits["training"], splits["development"], self.gym.target_name)
        for model in models:
            self.models[model.model_id] = model
            self.registry.record_fit(
                model,
                getattr(model, "hypothesis_id", model.model_id),
                "training",
                len(splits["training"]),
                artifact=model_to_artifact(model, splits["training"]),
                campaign_id=self.campaign_id,
            )
        return models

    def mechanism_score(self, hypothesis_id: str) -> dict[str, Any]:
        raise PermissionError(
            "Hidden-world mechanism truth is evaluator-only and intentionally unavailable through ResearchAPI"
        )

    def _model(self, model_id: str) -> Any:
        if model_id not in self.models:
            self._load_registry_state()
        if model_id not in self.models:
            raise KeyError(f"Model {model_id!r} is not fitted in this session or persisted registry")
        return self.models[model_id]

    def _load_registry_state(self) -> None:
        for payload in self.gym.ledger.payloads("hypothesis"):
            try:
                self.hypotheses[payload["hypothesis_id"]] = HypothesisSpec(**payload)
            except (KeyError, TypeError, ValueError):
                continue
        for payload in self.gym.ledger.payloads("model_fit"):
            if payload.get("campaign_id", "default") != self.campaign_id:
                continue
            artifact = payload.get("artifact", {})
            if not artifact or artifact.get("family") == "unknown":
                continue
            try:
                model = model_from_artifact(artifact)
            except (KeyError, TypeError, ValueError) as exc:
                self.artifact_load_errors.append(
                    {"model_id": str(payload.get("model_id", "unknown")), "error": str(exc)}
                )
                continue
            self.models[model.model_id] = model
