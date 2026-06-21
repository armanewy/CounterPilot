from __future__ import annotations

import _bootstrap  # noqa: F401

from pathlib import Path
import tempfile
import unittest

from behavior_lab.benchmarks.splits import chronological_split
from behavior_lab.datasets.nber_best_offer.normalize import build_sample_dataset, normalize_dataset
from behavior_lab.datasets.nber_best_offer.tasks import build_tasks
from behavior_lab.offerlab_research import OfferLabResearchAPI, ResearchBudgetError, ResearchPermissionError


def _splits() -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_sample_dataset(root / "raw")
        normalize_dataset(root / "raw", root / "normalized")
        rows = build_tasks(root / "normalized")["seller_next_action"]
        split = chronological_split(rows, time_key="timestamp")
        return split.train, split.development, split.hidden


class OfferLabResearchAPITests(unittest.TestCase):
    def test_api_exposes_only_permitted_data_and_blocks_forbidden_methods(self) -> None:
        train, dev, hidden = _splits()
        api = OfferLabResearchAPI(campaign_id="api-test", training_rows=train, development_rows=dev, hidden_rows=hidden)
        schema = api.inspect_schema()
        self.assertFalse(schema["production_export_allowed"])
        self.assertIn("query_hidden_data", dir(api))
        self.assertNotIn("hidden_rows", schema)
        preview = api.inspect_permitted_data(limit=2)
        self.assertLessEqual(len(preview), 2)
        with self.assertRaises(ResearchPermissionError):
            api.query_hidden_data()
        with self.assertRaises(ResearchPermissionError):
            api.execute_code("print('no')")
        with self.assertRaises(ResearchPermissionError):
            api.change_outcome("row", "accept")
        with self.assertRaises(ResearchPermissionError):
            api.set_budget(hidden_submissions=99)

    def test_development_and_hidden_budgets_are_enforced(self) -> None:
        train, dev, hidden = _splits()
        api = OfferLabResearchAPI(
            campaign_id="budget-test",
            training_rows=train,
            development_rows=dev,
            hidden_rows=hidden,
            development_evaluations=1,
            hidden_submissions=1,
        )
        proposal = api.register_formula(
            {
                "proposal_id": "p1",
                "terms": ["offer_to_asking_ratio"],
                "target_label": "counter",
                "falsification": "Fails if development log loss does not improve.",
            }
        )
        result = api.evaluate_development(proposal["proposal_id"])
        self.assertEqual(result["split"], "development")
        with self.assertRaises(ResearchBudgetError):
            api.evaluate_development(proposal["proposal_id"])
        hidden_result = api.submit_hidden_once(proposal["proposal_id"], lockbox_id="hidden-budget")
        self.assertEqual(hidden_result["hidden_submission_count"], 1)
        self.assertNotIn("row_id", hidden_result["predictions_redacted"][0])
        with self.assertRaises(ResearchBudgetError):
            api.submit_hidden_once(proposal["proposal_id"], lockbox_id="hidden-budget-2")

    def test_proposal_validation_blocks_unknown_budget_code_and_causal_claims(self) -> None:
        train, dev, hidden = _splits()
        api = OfferLabResearchAPI(campaign_id="validation-test", training_rows=train, development_rows=dev, hidden_rows=hidden)
        base = {"terms": ["offer_to_asking_ratio"], "target_label": "accept", "falsification": "Fails on development."}
        with self.assertRaises(ValueError):
            api.register_formula({**base, "terms": ["unknown_future_variable"]})
        with self.assertRaises(ResearchPermissionError):
            api.register_formula({**base, "proposal_id": "code", "code": "open('hidden')"})
        with self.assertRaises(ResearchPermissionError):
            api.register_formula({**base, "proposal_id": "budget", "budget": {"hidden": 10}})
        with self.assertRaises(ResearchPermissionError):
            api.register_formula({**base, "proposal_id": "causal", "claim": "causal lift"})
        with self.assertRaises(ResearchPermissionError):
            api.register_formula({**base, "proposal_id": "causal-string", "causal_claim": "true"})
        with self.assertRaises(ResearchPermissionError):
            api.register_formula({**base, "proposal_id": "causal-string-false", "causal_claim": "false"})
        with self.assertRaises(ResearchPermissionError):
            api.register_formula({**base, "proposal_id": "causal-falsification", "falsification": "Fails if causal effect disappears."})
        with self.assertRaises(ResearchBudgetError):
            api.register_formula({**base, "proposal_id": "complex", "terms": ["offer_to_asking_ratio"] * 9})

    def test_public_rows_and_budgets_are_not_mutable_internal_state(self) -> None:
        train, dev, hidden = _splits()
        api = OfferLabResearchAPI(campaign_id="immutable-api-test", training_rows=train, development_rows=dev, hidden_rows=hidden)
        copy_rows = api.training_rows
        copy_rows[0]["label"] = "tampered"
        self.assertNotEqual(api.training_rows[0]["label"], "tampered")
        with self.assertRaises(AttributeError):
            api.development_evaluations_remaining = 99  # type: ignore[misc]
        with self.assertRaises(AttributeError):
            api.hidden_submissions_remaining = 99  # type: ignore[misc]

    def test_hidden_used_ids_is_not_mutable_public_state(self) -> None:
        train, dev, hidden = _splits()
        api = OfferLabResearchAPI(campaign_id="hidden-state-test", training_rows=train, development_rows=dev, hidden_rows=hidden, hidden_submissions=2)
        proposal = api.register_formula(
            {
                "proposal_id": "p1",
                "terms": ["offer_to_asking_ratio"],
                "target_label": "counter",
                "falsification": "Fails if development log loss does not improve.",
            }
        )
        api.evaluate_development(proposal["proposal_id"])
        api.submit_hidden_once(proposal["proposal_id"], lockbox_id="same-lockbox")
        used = api.hidden_used_ids
        with self.assertRaises(AttributeError):
            used.clear()  # type: ignore[attr-defined]
        with self.assertRaises(ResearchBudgetError):
            api.submit_hidden_once(proposal["proposal_id"], lockbox_id="same-lockbox")


if __name__ == "__main__":
    unittest.main()
