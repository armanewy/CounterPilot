from __future__ import annotations

import _bootstrap  # noqa: F401

import unittest

from behavior_lab.datasets.craigslist_bargain.parser import evaluate_parser, parse_utterance, reconstruct_price_sequence


class NegotiationParserTests(unittest.TestCase):
    def test_extracts_offer_act_and_side_conditions(self) -> None:
        parsed = parse_utterance("How about $120 if I can pick up tonight?")
        self.assertEqual(parsed.offer_amount, 120.0)
        self.assertEqual(parsed.act, "counter")
        self.assertIn("pickup_or_meetup", parsed.side_conditions)

    def test_reconstructs_price_sequence(self) -> None:
        sequence = reconstruct_price_sequence(["I can do $80", "Could you meet me at $95?", "Deal"])
        self.assertEqual(sequence, [80.0, 95.0])

    def test_evaluation_is_language_extraction_only(self) -> None:
        report = evaluate_parser(
            [
                {"text": "Would you take $80?", "offer_amount": 80.0, "act": "propose"},
                {"text": "I accept.", "offer_amount": None, "act": "accept"},
            ]
        )
        self.assertEqual(report["evidence_role"], "LANGUAGE_EXTRACTION")
        self.assertFalse(report["production_export_allowed"])
        self.assertEqual(report["offer_accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
