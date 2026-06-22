from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
for path in [str(ROOT), str(TESTS)]:
    if path not in sys.path:
        sys.path.insert(0, path)

import _bootstrap  # noqa: E402,F401

from behavior_lab.money.accounting import (
    UnknownMaterialCostError,
    compute_decision_accounting,
    maximum_drawdown,
    summarize_money_entries,
)
from behavior_lab.money.contracts import Action, ContractValidationError, FinancialDecisionContract


DEADLINE = "2026-01-02T00:00:00+00:00"


def _contract() -> FinancialDecisionContract:
    return FinancialDecisionContract(
        contract_id="seller_contract",
        domain="seller",
        target={"metric": "mature_margin"},
        decision_horizon="7d",
        decision_deadline=DEADLINE,
        available_actions=[
            Action(action_id="abstain", action_type="no_action"),
            Action(action_id="accept", action_type="seller_response"),
            Action(action_id="change_price", action_type="price_change", action_mode="interventional"),
        ],
        no_action_id="abstain",
        payoff_specification={"metric": "net_margin"},
        cost_policy={"material_cost_fields": ["fees", "shipping"]},
        risk_policy={"paper_only": True},
        liquidity_policy={"not_applicable": True},
        resolution_source={"source": "seller_ledger"},
        data_cutoff_policy={"as_of": "decision_time"},
        prospective_requirement={"shadow_days": 30},
        notification_threshold={"enabled": False},
        paper_only=True,
        contract_version="v1",
    )


class ContractAccountingTests(unittest.TestCase):
    def test_contract_hash_and_reactive_action_boundary(self) -> None:
        contract = _contract()
        self.assertEqual({action.action_id for action in contract.automatic_evaluation_actions()}, {"abstain", "accept"})
        contract.assert_action_eligible_for_automatic_evaluation("accept")
        with self.assertRaises(ContractValidationError):
            contract.assert_action_eligible_for_automatic_evaluation("change_price")
        self.assertEqual(contract.contract_hash(), _contract().contract_hash())

    def test_contract_requires_no_action_in_available_actions(self) -> None:
        with self.assertRaises(ContractValidationError):
            FinancialDecisionContract(
                **{
                    **_contract().to_dict(),
                    "available_actions": [Action(action_id="accept", action_type="seller_response")],
                    "no_action_id": "abstain",
                }
            )

    def test_unknown_material_cost_is_ineligible_not_zero(self) -> None:
        result = compute_decision_accounting(
            gross_value=100.0,
            fees=12.0,
            shipping=None,
            refunds=5.0,
            material_cost_fields=["fees", "shipping", "refunds"],
        )
        self.assertFalse(result.eligible)
        self.assertIsNone(result.net_value)
        self.assertIn("shipping", result.missing_material_costs)
        with self.assertRaises(UnknownMaterialCostError):
            compute_decision_accounting(
                gross_value=100.0,
                fees=12.0,
                shipping=None,
                material_cost_fields=["shipping"],
                strict=True,
            )

    def test_accounting_costs_refunds_and_drawdown(self) -> None:
        result = compute_decision_accounting(
            gross_value=100.0,
            fees=10.0,
            slippage=1.0,
            shipping=8.0,
            refunds=15.0,
            cancellation_loss=2.0,
            return_loss=3.0,
            holding_costs=4.0,
            research_api_cost=0.5,
            uncertainty_adjustment=6.0,
            material_cost_fields=["fees", "shipping", "refunds"],
        )
        self.assertTrue(result.eligible)
        self.assertEqual(result.total_costs, 43.5)
        self.assertEqual(result.net_value, 56.5)
        self.assertEqual(result.conservative_expected_net_value, 50.5)
        self.assertEqual(maximum_drawdown([100, 90, 110, 80])["maximum_drawdown"], 30.0)

    def test_summary_refuses_to_mix_paper_and_real(self) -> None:
        entries = [
            {
                "decision_id": "a",
                "contract_hash": "c",
                "decision_timestamp": "2026-01-01T00:00:00+00:00",
                "selected_action": "abstain",
                "no_action_alternative": "abstain",
                "conservative_expected_net_value": 0.0,
                "designation": "paper",
                "provenance": {"strategy_id": "s", "source_id": "x"},
            },
            {
                "decision_id": "b",
                "contract_hash": "c",
                "decision_timestamp": "2026-01-02T00:00:00+00:00",
                "selected_action": "trade",
                "no_action_alternative": "cash",
                "conservative_expected_net_value": 1.0,
                "designation": "real",
                "provenance": {"strategy_id": "s", "source_id": "x"},
            },
        ]
        with self.assertRaises(ValueError):
            summarize_money_entries(entries)

    def test_summary_reports_capital_loss_frequency_and_value_groups(self) -> None:
        summary = summarize_money_entries(
            [
                {
                    "decision_id": "a",
                    "contract_hash": "contract_a",
                    "decision_timestamp": "2026-01-01T00:00:00+00:00",
                    "selected_action": "abstain",
                    "no_action_alternative": "abstain",
                    "capital_required": 10.0,
                    "maximum_possible_loss": 2.0,
                    "conservative_expected_net_value": -1.0,
                    "designation": "paper",
                    "provenance": {"strategy_id": "s1", "source_id": "src"},
                },
                {
                    "decision_id": "b",
                    "contract_hash": "contract_a",
                    "decision_timestamp": "2026-01-02T00:00:00+00:00",
                    "selected_action": "accept",
                    "no_action_alternative": "abstain",
                    "capital_required": 5.0,
                    "maximum_possible_loss": 4.0,
                    "realized_net_value": 3.0,
                    "designation": "paper",
                    "provenance": {"strategy_id": "s1", "source_id": "src"},
                },
            ]
        )
        self.assertEqual(summary["opportunity_count"], 2)
        self.assertEqual(summary["no_action_frequency"], 1)
        self.assertEqual(summary["capital_at_risk"], 15.0)
        self.assertEqual(summary["maximum_possible_loss"], 6.0)
        self.assertEqual(summary["value_by_contract"]["contract_a"], 2.0)


if __name__ == "__main__":
    unittest.main()
