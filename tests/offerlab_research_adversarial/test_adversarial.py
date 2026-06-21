from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
for path in [str(ROOT), str(TESTS)]:
    if path not in sys.path:
        sys.path.insert(0, path)

import _bootstrap  # noqa: F401

from behavior_lab.benchmarks.splits import chronological_split
from behavior_lab.datasets.nber_best_offer.normalize import build_sample_dataset, normalize_dataset
from behavior_lab.datasets.nber_best_offer.tasks import build_tasks
from behavior_lab.offerlab_research import AppendOnlyResearchStore, HypothesisAgent, OfferLabResearchAPI, ResearchLimits, ResearchPermissionError, ResearchScheduler


def _api() -> OfferLabResearchAPI:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_sample_dataset(root / "raw")
        normalize_dataset(root / "raw", root / "normalized")
        split = chronological_split(build_tasks(root / "normalized")["seller_next_action"], time_key="timestamp")
        return OfferLabResearchAPI(
            campaign_id="adv",
            training_rows=split.train,
            development_rows=split.development,
            hidden_rows=split.hidden,
            development_evaluations=2,
            store=AppendOnlyResearchStore(Path(tempfile.mkdtemp()) / "research.jsonl"),
        )


class Provider:
    def __init__(self, payload: list[dict[str, object]]) -> None:
        self.payload = payload

    def propose(self, request: dict[str, object]) -> list[dict[str, object]]:
        return self.payload


class OfferLabResearchAdversarialTests(unittest.TestCase):
    def test_agent_cannot_smuggle_hidden_or_mutation_requests(self) -> None:
        bad = [
            {"proposal_id": "hidden", "terms": ["offer_to_asking_ratio"], "hidden_data": True, "falsification": "Fails."},
            {"proposal_id": "mutate", "terms": ["offer_to_asking_ratio"], "change_outcome": True, "falsification": "Fails."},
        ]
        with self.assertRaises(ResearchPermissionError):
            HypothesisAgent(Provider(bad)).propose(_api())

    def test_agent_cannot_claim_causality_or_execute_code(self) -> None:
        with self.assertRaises(ResearchPermissionError):
            HypothesisAgent(Provider([{"proposal_id": "code", "terms": ["offer_to_asking_ratio"], "code": "import os", "falsification": "Fails."}])).propose(_api())
        with self.assertRaises(ResearchPermissionError):
            HypothesisAgent(Provider([{"proposal_id": "cause", "terms": ["offer_to_asking_ratio"], "claim": "causal effect", "falsification": "Fails."}])).propose(_api())

    def test_scheduler_rejects_unbounded_limits(self) -> None:
        with self.assertRaises(ValueError):
            ResearchScheduler(limits=ResearchLimits(cycles=0))


if __name__ == "__main__":
    unittest.main()
