from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import json
import math
from pathlib import Path
from typing import Any

from behavior_lab.core import new_id, stable_hash
from behavior_lab.dsl import Formula
from behavior_lab.offerlab_models.common import FEATURE_CONTRACT, FORBIDDEN_MODEL_FIELDS, PRODUCTION_EXPORT_ALLOWED, enriched_features, validate_feature_contract


class ResearchPermissionError(PermissionError):
    pass


class ResearchBudgetError(RuntimeError):
    pass


@dataclass(frozen=True)
class FormulaProposal:
    proposal_id: str
    terms: list[str]
    target_label: str
    falsification: str
    model_family: str = "logistic_formula"
    source: str = "agent"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AppendOnlyResearchStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.events: list[dict[str, Any]] = []
        if self.path and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.events.append(json.loads(line))

    def append(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        previous_hash = self.events[-1]["event_hash"] if self.events else "GENESIS"
        event = {
            "event_type": event_type,
            "payload": payload,
            "previous_hash": previous_hash,
        }
        event["event_hash"] = stable_hash(event)
        self.events.append(event)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    def by_campaign(self, campaign_id: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event.get("payload", {}).get("campaign_id") == campaign_id]


class OfferLabResearchAPI:
    """Narrow OfferLab autonomous-research facade.

    It exposes development evaluation and one-shot hidden submission for
    proposal artifacts. It deliberately does not expose hidden rows, raw source
    files, code execution, mutation of outcomes, budget changes, or production
    export.
    """

    def __init__(
        self,
        *,
        campaign_id: str,
        training_rows: list[dict[str, Any]],
        development_rows: list[dict[str, Any]],
        hidden_rows: list[dict[str, Any]],
        max_formula_terms: int = 8,
        development_evaluations: int = 20,
        hidden_submissions: int = 1,
        store: AppendOnlyResearchStore | None = None,
    ) -> None:
        if not campaign_id.strip():
            raise ValueError("campaign_id is required")
        if max_formula_terms <= 0:
            raise ValueError("max_formula_terms must be positive")
        if development_evaluations < 0 or hidden_submissions < 0:
            raise ValueError("budgets may not be negative")
        self.campaign_id = campaign_id
        self._training_rows = copy.deepcopy(list(training_rows))
        self._development_rows = copy.deepcopy(list(development_rows))
        self._hidden_rows = copy.deepcopy(list(hidden_rows))
        self.max_formula_terms = max_formula_terms
        self.store = store or AppendOnlyResearchStore()
        self._hidden_used_ids: set[str] = {
            str(event.get("payload", {}).get("result", {}).get("lockbox_id"))
            for event in self.store.by_campaign(campaign_id)
            if event.get("event_type") == "hidden_submitted"
        }
        self._hidden_used_ids.discard("")
        self._hidden_used_ids.discard("None")
        self._development_evaluations_remaining = development_evaluations
        self._hidden_submissions_remaining = max(0, hidden_submissions - len(self._hidden_used_ids))
        self.proposals: dict[str, FormulaProposal] = {}
        self.development_results: dict[str, dict[str, Any]] = {}
        if not validate_feature_contract(self._training_rows + self._development_rows + self._hidden_rows):
            raise ValueError("feature contract contains forbidden future/outcome/participant fields")
        self.store.append(
            "api_created",
            {
                "campaign_id": campaign_id,
                "training_rows": len(self._training_rows),
                "development_rows": len(self._development_rows),
                "hidden_rows_reserved": len(self._hidden_rows),
                "production_export_allowed": PRODUCTION_EXPORT_ALLOWED,
            },
        )

    @property
    def training_rows(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._training_rows)

    @property
    def development_rows(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._development_rows)

    @property
    def development_evaluations_remaining(self) -> int:
        return self._development_evaluations_remaining

    @property
    def hidden_submissions_remaining(self) -> int:
        return self._hidden_submissions_remaining

    @property
    def hidden_used_ids(self) -> frozenset[str]:
        return frozenset(self._hidden_used_ids)

    def inspect_schema(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "feature_contract": list(FEATURE_CONTRACT),
            "forbidden_features": sorted(FORBIDDEN_MODEL_FIELDS),
            "allowed_methods": [
                "inspect_schema",
                "list_variables",
                "inspect_permitted_data",
                "register_formula",
                "evaluate_development",
                "submit_hidden_once",
                "development_summary",
            ],
            "hidden_rows_reserved": len(self._hidden_rows),
            "production_export_allowed": PRODUCTION_EXPORT_ALLOWED,
            "security_boundary": "typed API only; not a sandbox for malicious in-process code",
        }

    def list_variables(self) -> list[str]:
        names = {
            name
            for row in self._training_rows
            for name, value in enriched_features(row).items()
            if name in FEATURE_CONTRACT and name not in FORBIDDEN_MODEL_FIELDS and isinstance(value, (int, float, bool))
        }
        return sorted(names)

    def inspect_permitted_data(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if limit < 0 or limit > 200:
            raise ValueError("limit must be between 0 and 200")
        output = []
        for row in self._training_rows[:limit]:
            features = {name: enriched_features(row).get(name) for name in self.list_variables()}
            output.append({"row_id": row.get("row_id"), "label": row.get("label"), "features": features})
        return output

    def query_hidden_data(self) -> None:
        raise ResearchPermissionError("hidden rows are not inspectable through OfferLabResearchAPI")

    def execute_code(self, _code: str) -> None:
        raise ResearchPermissionError("generated code execution is not available through OfferLabResearchAPI")

    def change_outcome(self, *_args: Any, **_kwargs: Any) -> None:
        raise ResearchPermissionError("outcomes are immutable through OfferLabResearchAPI")

    def set_budget(self, *_args: Any, **_kwargs: Any) -> None:
        raise ResearchPermissionError("agents may not choose or modify research budgets")

    def register_formula(self, proposal: dict[str, Any]) -> dict[str, Any]:
        parsed = self._parse_proposal(proposal)
        if parsed.proposal_id in self.proposals:
            raise ValueError(f"duplicate proposal_id {parsed.proposal_id!r}")
        self.proposals[parsed.proposal_id] = parsed
        self.store.append("proposal_registered", {"campaign_id": self.campaign_id, "proposal": parsed.to_dict()})
        return parsed.to_dict()

    def evaluate_development(self, proposal_id: str) -> dict[str, Any]:
        if self._development_evaluations_remaining <= 0:
            raise ResearchBudgetError("development evaluation budget exhausted")
        proposal = self._proposal(proposal_id)
        self._development_evaluations_remaining -= 1
        result = self._evaluate(proposal, self._development_rows, split="development")
        result["development_evaluations_remaining"] = self._development_evaluations_remaining
        self.development_results[proposal_id] = result
        self.store.append("development_evaluated", {"campaign_id": self.campaign_id, "result": result})
        return result

    def submit_hidden_once(self, proposal_id: str, *, lockbox_id: str) -> dict[str, Any]:
        if not lockbox_id.strip():
            raise ValueError("lockbox_id is required")
        if self._hidden_submissions_remaining <= 0 or lockbox_id in self._hidden_used_ids:
            raise ResearchBudgetError("hidden submission budget exhausted for this campaign/lockbox")
        proposal = self._proposal(proposal_id)
        if proposal_id not in self.development_results:
            raise ResearchPermissionError("proposal must be evaluated on development before hidden submission")
        self._hidden_submissions_remaining -= 1
        self._hidden_used_ids.add(lockbox_id)
        result = self._evaluate(proposal, self._hidden_rows, split="hidden")
        result["hidden_submission_count"] = 1
        result["lockbox_id"] = lockbox_id
        result["hidden_submissions_remaining"] = self._hidden_submissions_remaining
        self.store.append("hidden_submitted", {"campaign_id": self.campaign_id, "result": result})
        return result

    def development_summary(self) -> dict[str, Any]:
        ordered = sorted(self.development_results.values(), key=lambda item: (item["log_loss"], item["complexity"], item["proposal_id"]))
        return {
            "campaign_id": self.campaign_id,
            "evaluated": len(ordered),
            "best": ordered[0] if ordered else None,
            "development_evaluations_remaining": self._development_evaluations_remaining,
        }

    def promote(self, proposal_id: str, reason: str) -> None:
        self.store.append("proposal_promoted", {"campaign_id": self.campaign_id, "proposal_id": proposal_id, "reason": reason})

    def retire(self, proposal_id: str, reason: str) -> None:
        self.store.append("proposal_retired", {"campaign_id": self.campaign_id, "proposal_id": proposal_id, "reason": reason})

    def fail(self, proposal_id: str, reason: str) -> None:
        self.store.append("proposal_failed", {"campaign_id": self.campaign_id, "proposal_id": proposal_id, "reason": reason})

    def _parse_proposal(self, proposal: dict[str, Any]) -> FormulaProposal:
        if not isinstance(proposal, dict):
            raise ValueError("proposal must be an object")
        forbidden_keys = {"code", "python", "execute", "hidden_rows", "hidden_data", "budget", "set_budget", "change_outcome"}
        overlap = sorted(forbidden_keys & set(proposal))
        if overlap:
            raise ResearchPermissionError(f"proposal contains forbidden keys: {overlap}")
        if _has_causal_claim(proposal):
            raise ResearchPermissionError("autonomous proposals may not claim causality")
        terms = [str(term).strip() for term in proposal.get("terms", []) if str(term).strip()]
        if not terms:
            raise ValueError("proposal requires at least one formula term")
        if len(terms) > self.max_formula_terms:
            raise ResearchBudgetError("proposal exceeds max_formula_terms")
        formula = Formula.parse(terms)
        unknown = formula.variables - set(self.list_variables())
        if unknown:
            raise ValueError(f"proposal uses unavailable variables: {sorted(unknown)}")
        proposal_id = str(proposal.get("proposal_id") or new_id("offerlab_h"))
        falsification = str(proposal.get("falsification") or proposal.get("falsification_condition") or "").strip()
        if not falsification:
            raise ValueError("proposal requires a falsification condition")
        return FormulaProposal(
            proposal_id=proposal_id,
            terms=terms,
            target_label=str(proposal.get("target_label", "accept")),
            falsification=falsification,
            model_family=str(proposal.get("model_family", "logistic_formula")),
            source=str(proposal.get("source", "agent")),
        )

    def _proposal(self, proposal_id: str) -> FormulaProposal:
        try:
            return self.proposals[proposal_id]
        except KeyError as exc:
            raise KeyError(f"unknown proposal_id {proposal_id!r}") from exc

    def _evaluate(self, proposal: FormulaProposal, rows: list[dict[str, Any]], *, split: str) -> dict[str, Any]:
        formula = Formula.parse(proposal.terms)
        train_scores = [_score_formula(formula, row) for row in self._training_rows]
        positive = [score for score, row in zip(train_scores, self._training_rows, strict=True) if str(row["label"]) == proposal.target_label]
        negative = [score for score, row in zip(train_scores, self._training_rows, strict=True) if str(row["label"]) != proposal.target_label]
        threshold = (_mean(positive) + _mean(negative)) / 2.0
        base_rate = (len(positive) + 0.5) / (len(self._training_rows) + 1.0) if self._training_rows else 0.5
        total = 0.0
        predictions = []
        for row in rows:
            score = _score_formula(formula, row)
            probability = min(1.0 - 1e-6, max(1e-6, base_rate + 0.25 * math.tanh(score - threshold)))
            observed = 1.0 if str(row["label"]) == proposal.target_label else 0.0
            total -= observed * math.log(probability) + (1.0 - observed) * math.log(1.0 - probability)
            predictions.append({"row_id": row.get("row_id"), "probability": probability})
        redacted_predictions = (
            [{"index": index, "redacted": True} for index, _item in enumerate(predictions)]
            if split == "hidden"
            else [{"row_id": item["row_id"]} for item in predictions]
        )
        return {
            "campaign_id": self.campaign_id,
            "proposal_id": proposal.proposal_id,
            "split": split,
            "rows": len(rows),
            "target_label": proposal.target_label,
            "log_loss": total / len(rows) if rows else 0.0,
            "complexity": formula.complexity,
            "terms": list(proposal.terms),
            "predictions_redacted": redacted_predictions,
            "production_export_allowed": PRODUCTION_EXPORT_ALLOWED,
        }


def _score_formula(formula: Formula, row: dict[str, Any]) -> float:
    features = enriched_features(row)
    values = formula.vector(features)
    return sum(values[1:]) if len(values) > 1 else 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _has_causal_claim(proposal: dict[str, Any]) -> bool:
    causal_flag = proposal.get("causal_claim")
    if isinstance(causal_flag, bool) and causal_flag:
        return True
    if isinstance(causal_flag, str):
        return True
    text_fields = ["claim", "falsification", "falsification_condition", "model_family", "source"]
    return any("causal" in str(proposal.get(field, "")).lower() for field in text_fields)
