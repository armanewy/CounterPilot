from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from behavior_lab.counterpilot_reports import build_counterpilot_report, counterpilot_report_markdown
from behavior_lab.counterpilot_state import COUNTERPILOT_STATE_SCHEMA_VERSION, TransactionStateMachine, money


def _event(namespace: str, transaction_id: str, transition: str, event_id: str, occurred_at: str, **extra):
    body = {
        "schema_version": COUNTERPILOT_STATE_SCHEMA_VERSION,
        "event_id": event_id,
        "merchant_namespace": namespace,
        "transaction_id": transaction_id,
        "occurred_at": occurred_at,
        "received_at": extra.pop("received_at", occurred_at),
        "source": extra.pop("source", "test_fixture"),
        "idempotency_key": f"test_{event_id}",
        "transition_to": transition,
        "currency": "USD",
    }
    body.update(extra)
    return body


def _action_fields(action: str):
    return {
        "available_actions": [{"action": "accept"}, {"action": "counter"}, {"action": "decline"}, {"action": "create_checkout"}],
        "recommendation": {"system_mode": "manual_only", "recommendation_id": None},
        "merchant_decision": {"action": action, "actor": "merchant"},
        "executed_action": {"action": action},
    }


def _complete_transaction(namespace: str = "merchant_a:store_a", transaction_id: str = "txn_a"):
    return [
        _event(
            namespace,
            transaction_id,
            "offer_submitted",
            f"{transaction_id}_offer",
            "2026-06-22T10:00:00+00:00",
            line_items=[{"sku": "sku-refurb-pc", "quantity": 1, "unit_price": money(90000)}],
            economics={
                "buyer_offer": money(72000),
                "shipping_cost": money(3400),
                "cost_basis": money(52000),
                "inventory_age_days": 31,
                "merchant_floor": money(69000),
            },
        ),
        _event(
            namespace,
            transaction_id,
            "merchant_countered",
            f"{transaction_id}_counter",
            "2026-06-22T10:05:00+00:00",
            discounts=[{"type": "shipping", "amount": money(3400)}],
            economics={"counter_amount": money(76000), "shipping_cost": money(3400), "cost_basis": money(52000)},
            **_action_fields("counter"),
        ),
        _event(namespace, transaction_id, "buyer_accepted", f"{transaction_id}_buyer_accept", "2026-06-22T10:10:00+00:00"),
        _event(
            namespace,
            transaction_id,
            "checkout_created",
            f"{transaction_id}_checkout",
            "2026-06-22T10:11:00+00:00",
            checkout_reference={"kind": "draft_order_invoice"},
            **_action_fields("create_checkout"),
        ),
        _event(namespace, transaction_id, "order_created", f"{transaction_id}_order", "2026-06-22T10:12:00+00:00", source="shopify_webhook"),
        _event(
            namespace,
            transaction_id,
            "paid",
            f"{transaction_id}_paid",
            "2026-06-22T10:14:00+00:00",
            source="shopify_webhook",
            economics={"final_sale_price": money(76000), "shipping_charged": money(0)},
        ),
        _event(
            namespace,
            transaction_id,
            "partially_refunded",
            f"{transaction_id}_refund",
            "2026-06-22T10:16:00+00:00",
            source="shopify_webhook",
            economics={"refund_amount": money(1000)},
        ),
        _event(
            namespace,
            transaction_id,
            "mature",
            f"{transaction_id}_mature",
            "2026-07-22T10:20:00+00:00",
            mature_outcome={
                "payment_resolution": "partially_refunded",
                "refund_return_maturity_date": "2026-07-22T10:20:00+00:00",
                "reconciled_fees": money(2234),
                "reconciled_fulfillment_costs": money(4600),
                "mature_contribution_margin": money(16166),
            },
        ),
    ]


def _append_all(data_dir: Path, events):
    machine = TransactionStateMachine(data_dir)
    for event in events:
        machine.append_event(event)


class CounterpilotReportTests(unittest.TestCase):
    def test_funnel_counts_and_exact_mature_margin_arithmetic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            _append_all(data, _complete_transaction())

            report = build_counterpilot_report(data, merchant_namespace="merchant_a:store_a")

            self.assertEqual(report["offer_funnel"]["offers_submitted"], 1)
            self.assertEqual(report["offer_funnel"]["merchant_countered"], 1)
            self.assertEqual(report["offer_funnel"]["buyer_accepted"], 1)
            self.assertEqual(report["offer_funnel"]["checkout_created"], 1)
            self.assertEqual(report["offer_funnel"]["paid"], 1)
            self.assertEqual(report["offer_funnel"]["refunded"], 1)
            self.assertEqual(report["offer_funnel"]["mature"], 1)
            summary = report["mature_margin_summary"]
            self.assertFalse(summary["totals_provisional"])
            self.assertEqual(summary["gross_negotiated_revenue_minor"], 76000)
            self.assertEqual(summary["item_cost_minor"], 52000)
            self.assertEqual(summary["platform_payment_fees_minor"], 2234)
            self.assertEqual(summary["refund_amount_minor"], 1000)
            self.assertEqual(summary["mature_contribution_margin_minor"], 16166)

    def test_free_shipping_and_partial_refund_are_not_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            _append_all(data, _complete_transaction())

            report = build_counterpilot_report(data)

            self.assertEqual(report["mature_margin_summary"]["free_shipping_cost_minor"], 3400)
            self.assertEqual(report["mature_margin_summary"]["shipping_charged_minor"], 0)
            self.assertEqual(report["margin_leakage"]["free_shipping_minor"], 3400)
            self.assertEqual(report["margin_leakage"]["refunds_returns_minor"], 1000)

    def test_missing_cost_basis_marks_totals_provisional(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            events = _complete_transaction(transaction_id="txn_missing_cost")
            del events[0]["economics"]["cost_basis"]
            del events[1]["economics"]["cost_basis"]
            _append_all(data, events)

            report = build_counterpilot_report(data)

            self.assertTrue(report["mature_margin_summary"]["totals_provisional"])
            self.assertEqual(report["mature_margin_summary"]["complete_mature_transactions"], 0)
            self.assertEqual(report["margin_leakage"]["missing_cost_basis"], 1)

    def test_namespace_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            _append_all(data, _complete_transaction(namespace="merchant_a:store_a", transaction_id="txn_a"))
            _append_all(data, _complete_transaction(namespace="merchant_b:store_b", transaction_id="txn_b"))

            report = build_counterpilot_report(data, merchant_namespace="merchant_b:store_b")

            self.assertEqual(report["transaction_count"], 1)
            self.assertEqual(report["transactions"][0]["merchant_namespace"], "merchant_b:store_b")
            self.assertEqual(report["mature_margin_summary"]["gross_negotiated_revenue_minor"], 76000)

    def test_report_contains_no_pii(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            _append_all(data, _complete_transaction())

            rendered = json.dumps(build_counterpilot_report(data), sort_keys=True)

            self.assertNotIn("@", rendered)
            self.assertNotIn("buyer@example.com", rendered)

    def test_rule_simulator_is_labeled_non_causal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            _append_all(data, _complete_transaction())

            report = build_counterpilot_report(data, rule={"accept_above_asking_pct": 0.9, "counter_markup_pct": 0.1})

            self.assertIn("non-causal", report["rule_simulator"]["label"])
            self.assertEqual(report["rule_simulator"]["decision_counts"]["counter_at_amount"], 1)
            self.assertFalse(report["claims"]["causal_lift_claimed"])
            markdown = counterpilot_report_markdown(report)
            self.assertIn("Counterpilot Merchant Report", markdown)
            self.assertIn("Not a recommendation model", markdown)
            self.assertIn("Product And Inventory Breakdowns", markdown)


if __name__ == "__main__":
    unittest.main()
