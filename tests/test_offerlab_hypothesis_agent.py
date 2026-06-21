from __future__ import annotations

import _bootstrap  # noqa: F401

from pathlib import Path
import tempfile
import unittest

from behavior_lab.benchmarks.splits import chronological_split
from behavior_lab.datasets.nber_best_offer.normalize import build_sample_dataset, normalize_dataset
from behavior_lab.datasets.nber_best_offer.tasks import build_tasks
from behavior_lab.offerlab_research import DeterministicFakeProvider, HypothesisAgent, OfferLabResearchAPI


def _api() -> OfferLabResearchAPI:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    build_sample_dataset(root / "raw")
    normalize_dataset(root / "raw", root / "normalized")
    split = chronological_split(build_tasks(root / "normalized")["seller_next_action"], time_key="timestamp")
    api = OfferLabResearchAPI(campaign_id="agent-test", training_rows=split.train, development_rows=split.development, hidden_rows=split.hidden)
    tmp.cleanup()
    return api


class BadProvider:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def propose(self, request: dict[str, object]) -> object:
        self.request = request
        return self.payload


class OfferLabHypothesisAgentTests(unittest.TestCase):
    def test_fake_provider_registers_valid_formula_proposals(self) -> None:
        api = _api()
        agent = HypothesisAgent(DeterministicFakeProvider(), max_hypotheses=2)
        proposals = agent.propose(api)
        self.assertGreaterEqual(len(proposals), 1)
        self.assertTrue(all("terms" in proposal for proposal in proposals))
        self.assertFalse(any("hidden_rows" in event["payload"] for event in api.store.events))

    def test_agent_rejects_bad_provider_shapes_and_excess_candidates(self) -> None:
        with self.assertRaises(ValueError):
            HypothesisAgent(BadProvider({"not": "a list"})).propose(_api())
        too_many = [{"proposal_id": f"p{i}", "terms": ["offer_to_asking_ratio"], "falsification": "Fails."} for i in range(3)]
        with self.assertRaises(ValueError):
            HypothesisAgent(BadProvider(too_many), max_hypotheses=2).propose(_api())

    def test_provider_request_does_not_include_hidden_data_or_budget_controls(self) -> None:
        provider = BadProvider([{"proposal_id": "ok", "terms": ["offer_to_asking_ratio"], "falsification": "Fails."}])
        HypothesisAgent(provider).propose(_api())
        text = str(provider.request).lower()
        self.assertNotIn("hidden_rows': [", text)
        self.assertNotIn("set_budget", text)


if __name__ == "__main__":
    unittest.main()
