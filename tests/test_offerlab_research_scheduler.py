from __future__ import annotations

import _bootstrap  # noqa: F401

import json
from pathlib import Path
import tempfile
import unittest

from behavior_lab.benchmarks.splits import chronological_split
from behavior_lab.datasets.nber_best_offer.normalize import build_sample_dataset, normalize_dataset
from behavior_lab.datasets.nber_best_offer.tasks import build_tasks
from behavior_lab.offerlab_research import DeterministicFakeProvider, HypothesisAgent, ResearchLimits, ResearchScheduler


def _splits() -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    root = Path(tempfile.mkdtemp())
    build_sample_dataset(root / "raw")
    normalize_dataset(root / "raw", root / "normalized")
    split = chronological_split(build_tasks(root / "normalized")["seller_next_action"], time_key="timestamp")
    return split.train, split.development, split.hidden


class OfferLabResearchSchedulerTests(unittest.TestCase):
    def test_scheduler_respects_limits_and_persists_research_events(self) -> None:
        train, dev, hidden = _splits()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "events.jsonl"
            scheduler = ResearchScheduler(
                limits=ResearchLimits(cycles=1, hypotheses_per_cycle=2, mutations_per_cycle=1, development_evaluations=3, hidden_submissions=1, max_models=3),
                state_path=state,
            )
            report = scheduler.run(
                campaign_id="scheduler-a",
                training_rows=train,
                development_rows=dev,
                hidden_rows=hidden,
                agent=HypothesisAgent(DeterministicFakeProvider(), max_hypotheses=2),
            )
            self.assertLessEqual(report["evaluated_models"], 3)
            self.assertEqual(report["hidden_submissions"], 1)
            self.assertTrue(state.exists())
            events = [json.loads(line) for line in state.read_text(encoding="utf-8").splitlines()]
            event_types = {event["event_type"] for event in events}
            self.assertIn("proposal_registered", event_types)
            self.assertIn("development_evaluated", event_types)
            self.assertIn("hidden_submitted", event_types)
            self.assertIn("run_completed", event_types)

    def test_campaigns_are_separate_in_store(self) -> None:
        train, dev, hidden = _splits()
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = ResearchScheduler(limits=ResearchLimits(cycles=1, hypotheses_per_cycle=2, development_evaluations=2), state_path=Path(tmp) / "events.jsonl")
            agent = HypothesisAgent(DeterministicFakeProvider(), max_hypotheses=1)
            first = scheduler.run(campaign_id="campaign-one", training_rows=train, development_rows=dev, hidden_rows=hidden, agent=agent)
            second = scheduler.run(campaign_id="campaign-two", training_rows=train, development_rows=dev, hidden_rows=hidden, agent=agent)
            self.assertNotEqual(first["campaign_id"], second["campaign_id"])
            self.assertTrue(scheduler.store.by_campaign("campaign-one"))
            self.assertTrue(scheduler.store.by_campaign("campaign-two"))

    def test_hidden_lockbox_persists_across_scheduler_reruns(self) -> None:
        train, dev, hidden = _splits()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "events.jsonl"
            limits = ResearchLimits(cycles=1, hypotheses_per_cycle=1, mutations_per_cycle=1, development_evaluations=2, hidden_submissions=1)
            scheduler = ResearchScheduler(limits=limits, state_path=state)
            agent = HypothesisAgent(DeterministicFakeProvider(), max_hypotheses=1)
            first = scheduler.run(campaign_id="same-campaign", training_rows=train, development_rows=dev, hidden_rows=hidden, agent=agent)
            second = scheduler.run(campaign_id="same-campaign", training_rows=train, development_rows=dev, hidden_rows=hidden, agent=agent)
            self.assertEqual(first["hidden_submissions"], 1)
            self.assertEqual(second["hidden_submissions"], 0)
            self.assertTrue(any("hidden submission budget exhausted" in failure["error"] for failure in second["failures"]))

    def test_scheduler_rejects_over_limit_agent_before_registration(self) -> None:
        train, dev, hidden = _splits()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "events.jsonl"
            scheduler = ResearchScheduler(
                limits=ResearchLimits(cycles=1, hypotheses_per_cycle=2, mutations_per_cycle=1, development_evaluations=3),
                state_path=state,
            )
            report = scheduler.run(
                campaign_id="over-limit",
                training_rows=train,
                development_rows=dev,
                hidden_rows=hidden,
                agent=HypothesisAgent(DeterministicFakeProvider(), max_hypotheses=3),
            )
            self.assertEqual(report["evaluated_models"], 0)
            self.assertIn("proposal cap", report["failures"][0]["error"])
            events = [json.loads(line) for line in state.read_text(encoding="utf-8").splitlines()]
            self.assertNotIn("proposal_registered", {event["event_type"] for event in events})


if __name__ == "__main__":
    unittest.main()
