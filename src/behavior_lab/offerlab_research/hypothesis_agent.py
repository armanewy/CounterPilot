from __future__ import annotations

from typing import Any, Protocol

from behavior_lab.offerlab_research.api import OfferLabResearchAPI, ResearchPermissionError


class ProposalProvider(Protocol):
    def propose(self, request: dict[str, Any]) -> list[dict[str, Any]]: ...


class DeterministicFakeProvider:
    def propose(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        variables = list(request.get("variables", []))
        first = variables[0] if variables else "offer_to_asking_ratio"
        second = variables[1] if len(variables) > 1 else first
        return [
            {
                "proposal_id": "fake_relative_offer",
                "terms": [first],
                "target_label": "accept",
                "falsification": "Fails if the formula does not improve chronological development log loss.",
            },
            {
                "proposal_id": "fake_interaction",
                "terms": [first, second, f"interaction({first}, {second})"],
                "target_label": "counter",
                "falsification": "Fails if the interaction does not beat simpler development candidates.",
            },
        ][: int(request.get("max_hypotheses", 2))]


class HypothesisAgent:
    def __init__(self, provider: ProposalProvider, *, max_hypotheses: int = 5) -> None:
        if max_hypotheses <= 0:
            raise ValueError("max_hypotheses must be positive")
        self.provider = provider
        self.max_hypotheses = max_hypotheses

    def propose(self, api: OfferLabResearchAPI) -> list[dict[str, Any]]:
        request = {
            "schema": api.inspect_schema(),
            "variables": api.list_variables(),
            "training_preview": api.inspect_permitted_data(limit=10),
            "development_summary": api.development_summary(),
            "max_hypotheses": self.max_hypotheses,
            "rules": [
                "Use only listed variables.",
                "Return formula terms, model family, and falsification condition.",
                "Do not request hidden data.",
                "Do not execute code.",
                "Do not mutate outcomes.",
                "Do not choose budgets.",
                "Do not claim causality.",
            ],
        }
        payload = self.provider.propose(request)
        if not isinstance(payload, list):
            raise ValueError("provider must return a list")
        if len(payload) > self.max_hypotheses:
            raise ValueError("provider returned more hypotheses than allowed")
        accepted = []
        for item in payload:
            registered = api.register_formula(dict(item))
            accepted.append(registered)
        return accepted

    def attempt_forbidden_hidden_read(self, api: OfferLabResearchAPI) -> None:
        raise ResearchPermissionError(str(api.query_hidden_data()))
