from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from behavior_lab.offerlab_research.api import AppendOnlyResearchStore, OfferLabResearchAPI, ResearchBudgetError
from behavior_lab.offerlab_research.hypothesis_agent import HypothesisAgent


@dataclass(frozen=True)
class ResearchLimits:
    cycles: int = 3
    hypotheses_per_cycle: int = 5
    mutations_per_cycle: int = 2
    development_evaluations: int = 20
    hidden_submissions: int = 1
    prospective_submissions: int = 1
    max_formula_terms: int = 8
    max_models: int = 20
    max_runtime_seconds: float = 10.0

    def validate(self) -> None:
        values = self.__dict__
        for name, value in values.items():
            if value <= 0 and name not in {"prospective_submissions"}:
                raise ValueError(f"{name} must be positive")


class ResearchScheduler:
    def __init__(self, *, limits: ResearchLimits | None = None, state_path: str | Path | None = None) -> None:
        self.limits = limits or ResearchLimits()
        self.limits.validate()
        if state_path is None:
            raise ValueError("ResearchScheduler requires an explicit file-backed state_path")
        self.store = AppendOnlyResearchStore(state_path)

    def run(
        self,
        *,
        campaign_id: str,
        training_rows: list[dict[str, Any]],
        development_rows: list[dict[str, Any]],
        hidden_rows: list[dict[str, Any]],
        agent: HypothesisAgent,
    ) -> dict[str, Any]:
        start = monotonic()
        api = OfferLabResearchAPI(
            campaign_id=campaign_id,
            training_rows=training_rows,
            development_rows=development_rows,
            hidden_rows=hidden_rows,
            max_formula_terms=self.limits.max_formula_terms,
            development_evaluations=self.limits.development_evaluations,
            hidden_submissions=self.limits.hidden_submissions,
            store=self.store,
        )
        evaluated = []
        failures = []
        stopped_reason = None
        per_cycle_proposal_cap = min(self.limits.hypotheses_per_cycle, self.limits.mutations_per_cycle + 1)
        for cycle in range(1, self.limits.cycles + 1):
            if monotonic() - start > self.limits.max_runtime_seconds:
                stopped_reason = "runtime limit"
                self.store.append("run_stopped", {"campaign_id": campaign_id, "reason": stopped_reason, "cycle": cycle})
                break
            if agent.max_hypotheses > per_cycle_proposal_cap:
                stopped_reason = "agent proposal cap exceeds scheduler hypothesis/mutation limits"
                failure = {"cycle": cycle, "error": stopped_reason}
                failures.append(failure)
                self.store.append("proposal_failed", {"campaign_id": campaign_id, **failure})
                break
            try:
                proposals = agent.propose(api)
            except Exception as exc:  # provider validation failures are persisted
                failures.append({"cycle": cycle, "error": str(exc)})
                self.store.append("proposal_failed", {"campaign_id": campaign_id, "cycle": cycle, "reason": str(exc)})
                continue
            if monotonic() - start > self.limits.max_runtime_seconds:
                stopped_reason = "runtime limit"
                self.store.append("run_stopped", {"campaign_id": campaign_id, "reason": stopped_reason, "cycle": cycle})
                break
            if len(proposals) > self.limits.hypotheses_per_cycle:
                stopped_reason = "agent exceeded hypotheses_per_cycle"
                failure = {"cycle": cycle, "error": stopped_reason}
                failures.append(failure)
                self.store.append("proposal_failed", {"campaign_id": campaign_id, **failure})
                break
            if max(0, len(proposals) - 1) > self.limits.mutations_per_cycle:
                stopped_reason = "agent exceeded mutations_per_cycle"
                failure = {"cycle": cycle, "error": stopped_reason}
                failures.append(failure)
                self.store.append("proposal_failed", {"campaign_id": campaign_id, **failure})
                break
            for proposal in proposals:
                if monotonic() - start > self.limits.max_runtime_seconds:
                    stopped_reason = "runtime limit"
                    self.store.append("run_stopped", {"campaign_id": campaign_id, "reason": stopped_reason, "cycle": cycle})
                    break
                if len(evaluated) >= self.limits.max_models:
                    break
                try:
                    result = api.evaluate_development(proposal["proposal_id"])
                except Exception as exc:
                    failures.append({"proposal_id": proposal["proposal_id"], "error": str(exc)})
                    api.fail(proposal["proposal_id"], str(exc))
                    continue
                evaluated.append(result)
            if stopped_reason:
                break
            if len(evaluated) >= self.limits.max_models:
                break
        evaluated.sort(key=lambda item: (item["log_loss"], item["complexity"], item["proposal_id"]))
        hidden_result = None
        if evaluated and self.limits.hidden_submissions and not stopped_reason and monotonic() - start <= self.limits.max_runtime_seconds:
            winner = evaluated[0]
            api.promote(winner["proposal_id"], "best development result before hidden submission")
            for loser in evaluated[-self.limits.mutations_per_cycle :]:
                if loser["proposal_id"] != winner["proposal_id"]:
                    api.retire(loser["proposal_id"], "dominated by development winner")
            try:
                hidden_result = api.submit_hidden_once(winner["proposal_id"], lockbox_id=f"{campaign_id}:hidden")
            except ResearchBudgetError as exc:
                failure = {"proposal_id": winner["proposal_id"], "error": str(exc)}
                failures.append(failure)
                self.store.append("hidden_submission_failed", {"campaign_id": campaign_id, **failure})
        elif evaluated and self.limits.hidden_submissions and monotonic() - start > self.limits.max_runtime_seconds:
            stopped_reason = "runtime limit"
            self.store.append("run_stopped", {"campaign_id": campaign_id, "reason": stopped_reason, "cycle": "hidden"})
        summary = {
            "campaign_id": campaign_id,
            "cycles_requested": self.limits.cycles,
            "evaluated_models": len(evaluated),
            "failures": failures,
            "best_development": evaluated[0] if evaluated else None,
            "hidden_result": hidden_result,
            "hidden_submissions": 1 if hidden_result else 0,
            "prospective_submissions": 0,
            "stopped_reason": stopped_reason,
            "events_persisted": len(self.store.by_campaign(campaign_id)),
        }
        self.store.append("run_completed", summary)
        return summary
