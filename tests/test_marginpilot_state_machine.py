from __future__ import annotations

import tempfile
import unittest

import _bootstrap  # noqa: F401

from behavior_lab.marginpilot_state import (
    MARGINPILOT_STATE_SCHEMA_VERSION,
    MarginPilotStateError,
    TransactionStateMachine,
    money,
)


def _event(
    transition_to: str,
    *,
    event_id: str,
    occurred_at: str,
    merchant_namespace: str = "merchant_a",
    transaction_id: str = "txn_001",
    source: str = "local",
    idempotency_key: str | None = None,
    **extra: object,
) -> dict:
    payload = {
        "schema_version": MARGINPILOT_STATE_SCHEMA_VERSION,
        "event_id": event_id,
        "merchant_namespace": merchant_namespace,
        "transaction_id": transaction_id,
        "occurred_at": occurred_at,
        "received_at": extra.pop("received_at", occurred_at),
        "source": source,
        "idempotency_key": idempotency_key or f"idem_{event_id}",
        "transition_to": transition_to,
        "currency": "USD",
    }
    payload.update(extra)
    return payload


def _actions(action: str = "counter") -> dict:
    return {
        "available_actions": [
            {"action": "accept"},
            {"action": "decline"},
            {"action": "counter"},
            {"action": "create_checkout"},
            {"action": "cancel"},
        ],
        "recommendation": {"kind": "none", "system_mode": "manual_only"},
        "merchant_decision": {"action": action, "actor": "merchant"},
        "executed_action": {"action": action},
    }


def _submit(event_id: str = "e_offer", merchant_namespace: str = "merchant_a", transaction_id: str = "txn_001") -> dict:
    return _event(
        "offer_submitted",
        event_id=event_id,
        merchant_namespace=merchant_namespace,
        transaction_id=transaction_id,
        occurred_at="2026-06-22T10:00:00+00:00",
        line_items=[{"sku": "sku-1", "quantity": 1, "unit_price": money(90000)}],
        economics={"buyer_offer": money(72000), "shipping_cost": money(3400)},
    )


class MarginPilotStateMachineTests(unittest.TestCase):
    def test_complete_local_commerce_path_reaches_mature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            machine = TransactionStateMachine(tmp)
            machine.append_event(_submit())
            machine.append_event(
                _event(
                    "merchant_countered",
                    event_id="e_counter",
                    occurred_at="2026-06-22T10:05:00+00:00",
                    discounts=[{"type": "shipping", "amount": money(3400)}],
                    economics={"counter_amount": money(76000), "shipping_cost": money(3400)},
                    **_actions("counter"),
                )
            )
            machine.append_event(_event("buyer_accepted", event_id="e_buyer_accept", occurred_at="2026-06-22T10:10:00+00:00", source="buyer"))
            machine.append_event(
                _event(
                    "checkout_created",
                    event_id="e_checkout",
                    occurred_at="2026-06-22T10:11:00+00:00",
                    checkout_reference={"kind": "draft_order_invoice"},
                    **_actions("create_checkout"),
                )
            )
            machine.append_event(_event("order_created", event_id="e_order", occurred_at="2026-06-22T10:12:00+00:00", source="shopify_webhook"))
            machine.append_event(_event("payment_pending", event_id="e_pending", occurred_at="2026-06-22T10:13:00+00:00", source="shopify_webhook"))
            machine.append_event(_event("paid", event_id="e_paid", occurred_at="2026-06-22T10:14:00+00:00", source="shopify_webhook"))
            machine.append_event(
                _event(
                    "mature",
                    event_id="e_mature",
                    occurred_at="2026-07-22T10:14:00+00:00",
                    mature_outcome={
                        "payment_resolution": "paid",
                        "refund_return_maturity_date": "2026-07-22T10:14:00+00:00",
                        "reconciled_fees": money(2234),
                        "reconciled_fulfillment_costs": money(4600),
                        "mature_contribution_margin": money(17166),
                    },
                )
            )

            snapshot = machine.inspect("merchant_a", "txn_001")

            self.assertEqual(snapshot["current_state"], "mature")
            self.assertFalse(snapshot["pending_event_ids"])
            self.assertEqual(snapshot["mature_outcome"]["mature_contribution_margin"], money(17166))

    def test_duplicated_webhook_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            machine = TransactionStateMachine(tmp)
            machine.append_event(_submit())
            paid = _event("order_created", event_id="e_order", occurred_at="2026-06-22T10:12:00+00:00", source="shopify_webhook")
            machine.append_event(_event("merchant_accepted", event_id="e_accept", occurred_at="2026-06-22T10:01:00+00:00", **_actions("accept")))
            machine.append_event(_event("checkout_created", event_id="e_checkout", occurred_at="2026-06-22T10:02:00+00:00", **_actions("create_checkout")))
            first = machine.append_event(paid)
            second = machine.append_event(dict(paid))

            self.assertTrue(first.imported)
            self.assertTrue(second.idempotent_replay)
            self.assertEqual(machine.inspect("merchant_a", "txn_001")["event_count"], 4)

    def test_refund_before_local_order_created_is_stored_then_reconciled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            machine = TransactionStateMachine(tmp)
            machine.append_event(_submit())
            machine.append_event(_event("merchant_accepted", event_id="e_accept", occurred_at="2026-06-22T10:01:00+00:00", **_actions("accept")))
            machine.append_event(_event("checkout_created", event_id="e_checkout", occurred_at="2026-06-22T10:02:00+00:00", **_actions("create_checkout")))
            machine.append_event(
                _event(
                    "partially_refunded",
                    event_id="e_refund",
                    occurred_at="2026-06-22T10:15:00+00:00",
                    received_at="2026-06-22T10:03:00+00:00",
                    source="shopify_webhook",
                    economics={"refund_amount": money(1000)},
                )
            )
            self.assertEqual(machine.inspect("merchant_a", "txn_001")["pending_event_ids"], ["e_refund"])

            machine.append_event(_event("order_created", event_id="e_order", occurred_at="2026-06-22T10:10:00+00:00", source="shopify_webhook"))
            machine.append_event(_event("paid", event_id="e_paid", occurred_at="2026-06-22T10:12:00+00:00", source="shopify_webhook"))

            snapshot = machine.inspect("merchant_a", "txn_001")
            self.assertEqual(snapshot["current_state"], "partially_refunded")
            self.assertFalse(snapshot["pending_event_ids"])

    def test_buyer_acceptance_after_expiration_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            machine = TransactionStateMachine(tmp)
            machine.append_event(_submit())
            machine.append_event(_event("offer_expired", event_id="e_expire", occurred_at="2026-06-22T10:30:00+00:00"))
            with self.assertRaises(MarginPilotStateError):
                machine.append_event(_event("buyer_accepted", event_id="e_late", occurred_at="2026-06-22T10:31:00+00:00", source="buyer"))

    def test_two_concurrent_counters_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            machine = TransactionStateMachine(tmp)
            machine.append_event(_submit())
            machine.append_event(_event("merchant_countered", event_id="e_counter_1", occurred_at="2026-06-22T10:05:00+00:00", **_actions("counter")))
            with self.assertRaises(MarginPilotStateError):
                machine.append_event(_event("merchant_countered", event_id="e_counter_2", occurred_at="2026-06-22T10:05:01+00:00", **_actions("counter")))

    def test_cancellation_after_payment_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            machine = TransactionStateMachine(tmp)
            machine.append_event(_submit())
            machine.append_event(_event("merchant_accepted", event_id="e_accept", occurred_at="2026-06-22T10:01:00+00:00", **_actions("accept")))
            machine.append_event(_event("checkout_created", event_id="e_checkout", occurred_at="2026-06-22T10:02:00+00:00", **_actions("create_checkout")))
            machine.append_event(_event("order_created", event_id="e_order", occurred_at="2026-06-22T10:03:00+00:00", source="shopify_webhook"))
            machine.append_event(_event("paid", event_id="e_paid", occurred_at="2026-06-22T10:04:00+00:00", source="shopify_webhook"))
            with self.assertRaises(MarginPilotStateError):
                machine.append_event(_event("cancelled", event_id="e_cancel", occurred_at="2026-06-22T10:05:00+00:00", source="shopify_webhook", **_actions("cancel")))

    def test_partial_refund_full_refund_and_return_reopening(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            machine = TransactionStateMachine(tmp)
            machine.append_event(_submit())
            machine.append_event(_event("merchant_accepted", event_id="e_accept", occurred_at="2026-06-22T10:01:00+00:00", **_actions("accept")))
            machine.append_event(_event("checkout_created", event_id="e_checkout", occurred_at="2026-06-22T10:02:00+00:00", **_actions("create_checkout")))
            machine.append_event(_event("order_created", event_id="e_order", occurred_at="2026-06-22T10:03:00+00:00", source="shopify_webhook"))
            machine.append_event(_event("paid", event_id="e_paid", occurred_at="2026-06-22T10:04:00+00:00", source="shopify_webhook"))
            machine.append_event(_event("partially_refunded", event_id="e_partial", occurred_at="2026-06-22T10:05:00+00:00", source="shopify_webhook"))
            machine.append_event(_event("fully_refunded", event_id="e_full", occurred_at="2026-06-22T10:06:00+00:00", source="shopify_webhook"))
            machine.append_event(_event("return_opened", event_id="e_return_1", occurred_at="2026-06-22T10:07:00+00:00", source="shopify_webhook"))
            machine.append_event(_event("return_received", event_id="e_return_recv", occurred_at="2026-06-22T10:08:00+00:00", source="shopify_webhook"))
            machine.append_event(_event("return_closed", event_id="e_return_close", occurred_at="2026-06-22T10:09:00+00:00", source="shopify_webhook"))
            machine.append_event(_event("return_opened", event_id="e_return_reopen", occurred_at="2026-06-22T10:10:00+00:00", source="shopify_webhook"))

            self.assertEqual(machine.inspect("merchant_a", "txn_001")["current_state"], "return_opened")

    def test_currency_mismatch_requires_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            machine = TransactionStateMachine(tmp)
            machine.append_event(_submit())
            with self.assertRaises(MarginPilotStateError):
                machine.append_event(
                    _event(
                        "merchant_accepted",
                        event_id="e_eur",
                        occurred_at="2026-06-22T10:01:00+00:00",
                        economics={"accepted_amount": money(70000, "EUR")},
                        **_actions("accept"),
                    )
                )

            machine.append_event(
                _event(
                    "merchant_accepted",
                    event_id="e_eur_converted",
                    occurred_at="2026-06-22T10:01:00+00:00",
                    economics={"accepted_amount": money(70000, "EUR")},
                    currency_conversion={"from_currency": "EUR", "to_currency": "USD", "rate_basis": "fixture", "source": "manual"},
                    **_actions("accept"),
                )
            )
            self.assertEqual(machine.inspect("merchant_a", "txn_001")["current_state"], "merchant_accepted")

    def test_replay_after_restart_preserves_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            machine = TransactionStateMachine(tmp)
            machine.append_event(_submit())
            machine.append_event(_event("merchant_countered", event_id="e_counter", occurred_at="2026-06-22T10:05:00+00:00", **_actions("counter")))

            restarted = TransactionStateMachine(tmp)

            self.assertEqual(restarted.inspect("merchant_a", "txn_001")["current_state"], "merchant_countered")

    def test_cross_merchant_transaction_id_collision_is_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            machine = TransactionStateMachine(tmp)
            machine.append_event(_submit(merchant_namespace="merchant_a", transaction_id="shared_txn"))
            machine.append_event(_submit(event_id="e_offer_b", merchant_namespace="merchant_b", transaction_id="shared_txn"))
            machine.append_event(
                _event(
                    "merchant_declined",
                    event_id="e_decline_b",
                    merchant_namespace="merchant_b",
                    transaction_id="shared_txn",
                    occurred_at="2026-06-22T10:01:00+00:00",
                    **_actions("decline"),
                )
            )

            self.assertEqual(machine.inspect("merchant_a", "shared_txn")["current_state"], "offer_submitted")
            self.assertEqual(machine.inspect("merchant_b", "shared_txn")["current_state"], "merchant_declined")

    def test_rejects_pii_and_free_form_buyer_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            machine = TransactionStateMachine(tmp)
            bad = _submit()
            bad["buyer_message"] = "please email person@example.com"
            with self.assertRaises(MarginPilotStateError):
                machine.append_event(bad)

    def test_mature_requires_reconciled_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            machine = TransactionStateMachine(tmp)
            machine.append_event(_submit())
            machine.append_event(_event("merchant_accepted", event_id="e_accept", occurred_at="2026-06-22T10:01:00+00:00", **_actions("accept")))
            machine.append_event(_event("checkout_created", event_id="e_checkout", occurred_at="2026-06-22T10:02:00+00:00", **_actions("create_checkout")))
            machine.append_event(_event("order_created", event_id="e_order", occurred_at="2026-06-22T10:03:00+00:00", source="shopify_webhook"))
            machine.append_event(_event("paid", event_id="e_paid", occurred_at="2026-06-22T10:04:00+00:00", source="shopify_webhook"))
            with self.assertRaises(MarginPilotStateError):
                machine.append_event(
                    _event(
                        "mature",
                        event_id="e_bad_mature",
                        occurred_at="2026-07-22T10:04:00+00:00",
                        mature_outcome={"payment_resolution": "paid"},
                    )
                )


if __name__ == "__main__":
    unittest.main()
