from __future__ import annotations

import tempfile
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
for path in [str(ROOT), str(TESTS)]:
    if path not in sys.path:
        sys.path.insert(0, path)

import _bootstrap  # noqa: E402,F401

from behavior_lab.money.ledger import MoneyLedger
from behavior_lab.money.offerlab_adapter import offerlab_shadow_contract, offerlab_shadow_entry
from behavior_lab.offerlab_pilot import audit_pilot, import_pilot, shadow_report_pilot
from test_offerlab_pilot import _pilot_files


class OfferLabMoneyAdapterTests(unittest.TestCase):
    def test_offerlab_shadow_entry_preserves_seller_accounting_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as input_tmp, tempfile.TemporaryDirectory() as data_tmp:
            source = Path(input_tmp)
            _pilot_files(source)
            import_pilot(source, data_root=data_tmp, pilot_id="money_adapter")
            audit = audit_pilot("money_adapter", data_root=data_tmp)
            shadow = shadow_report_pilot("money_adapter", data_root=data_tmp)
            contract = offerlab_shadow_contract(
                pilot_id="money_adapter",
                decision_deadline="2026-03-01T00:00:00+00:00",
            )

            entry = offerlab_shadow_entry(
                audit=audit,
                shadow_report=shadow,
                contract=contract,
                decision_timestamp="2026-02-20T00:00:00+00:00",
                data_cutoff="2026-02-19T23:00:00+00:00",
            )

            self.assertEqual(entry.designation, "paper")
            self.assertEqual(entry.selected_action, "abstain")
            self.assertFalse(entry.provenance["executes_seller_actions"])
            self.assertFalse(entry.provenance["causal_lift_claimed"])
            self.assertEqual(entry.provenance["mature_contribution_margin"]["total"], 900.0)
            self.assertEqual(entry.provenance["cancellation_return_effects"]["total_refunds"], 0.0)
            self.assertIn("shipping_coverage", entry.provenance["readiness_gate"]["observed"])
            self.assertEqual(entry.mechanically_defined_no_action_outcome["seller_mutation"], False)

            ledger = MoneyLedger(str(Path(data_tmp) / "money.jsonl"))
            ledger.append_entry(entry)
            self.assertTrue(ledger.verify())

    def test_offerlab_shadow_entry_marks_unknown_costs_ineligible(self) -> None:
        with tempfile.TemporaryDirectory() as input_tmp, tempfile.TemporaryDirectory() as data_tmp:
            source = Path(input_tmp)
            _pilot_files(source, omit_last_cost=True)
            import_pilot(source, data_root=data_tmp, pilot_id="money_missing_cost")
            audit = audit_pilot("money_missing_cost", data_root=data_tmp)
            shadow = shadow_report_pilot("money_missing_cost", data_root=data_tmp)
            contract = offerlab_shadow_contract(
                pilot_id="money_missing_cost",
                decision_deadline="2026-03-01T00:00:00+00:00",
            )

            entry = offerlab_shadow_entry(
                audit=audit,
                shadow_report=shadow,
                contract=contract,
                decision_timestamp="2026-02-20T00:00:00+00:00",
                data_cutoff="2026-02-19T23:00:00+00:00",
            )

            self.assertFalse(entry.material_costs_known)
            self.assertIsNone(entry.conservative_expected_net_value)
            self.assertIn("unknown_material_seller_costs", entry.ineligibility_reasons)
            self.assertTrue(entry.provenance["data_quality_gaps"]["raw_identifiers_redacted"])


if __name__ == "__main__":
    unittest.main()
