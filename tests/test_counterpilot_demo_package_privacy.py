from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]

DEMO_ARTIFACTS = [
    ROOT / "docs" / "COUNTERPILOT_PRIVATE_BETA_DEMO_PACKAGE.md",
    ROOT / "docs" / "COUNTERPILOT_DEMO_SCRIPT.md",
    ROOT / "docs" / "COUNTERPILOT_ONE_PAGER.md",
    ROOT / "docs" / "COUNTERPILOT_PRICING_HYPOTHESIS.md",
    ROOT / "docs" / "COUNTERPILOT_RECORDING_CHECKLIST.md",
    ROOT / "docs" / "COUNTERPILOT_PRIVATE_BETA_OUTREACH.md",
    ROOT / "reports" / "counterpilot_private_beta_sample_report.md",
]

FORBIDDEN_PATTERNS = {
    "raw_shopify_gid": re.compile(r"gid://shopify/", re.IGNORECASE),
    "checkout_or_order_status_url": re.compile(r"https?://\S+", re.IGNORECASE),
    "email_address": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    "phone_number": re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(\d{3}\)\s*|\d{3}[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)"),
    "ip_address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "raw_secret_or_token": re.compile(r"\b(?:shpss|shpat|access|refresh|token|secret)[-_A-Za-z0-9]{8,}\b", re.IGNORECASE),
    "raw_buyer_message": re.compile(r"raw buyer message:\s*.+", re.IGNORECASE),
}


class CounterpilotDemoPackagePrivacyTests(unittest.TestCase):
    def test_demo_package_artifacts_are_present(self) -> None:
        missing = [str(path.relative_to(ROOT)) for path in DEMO_ARTIFACTS if not path.exists()]
        self.assertEqual(missing, [])

    def test_demo_package_has_no_raw_identifiers_or_pii(self) -> None:
        failures: list[str] = []
        for path in DEMO_ARTIFACTS:
            text = path.read_text(encoding="utf-8")
            for name, pattern in FORBIDDEN_PATTERNS.items():
                match = pattern.search(text)
                if match:
                    failures.append(f"{path.relative_to(ROOT)}:{name}:{match.group(0)}")
        self.assertEqual(failures, [])

    def test_sample_report_keeps_required_boundaries(self) -> None:
        text = (ROOT / "reports" / "counterpilot_private_beta_sample_report.md").read_text(encoding="utf-8")
        self.assertIn("Production evidence: false", text)
        self.assertIn("does not estimate conversion lift, profit lift", text)
        self.assertIn("Counterpilot is not a recommendation model", text)
        self.assertIn("## Mature Margin Summary", text)
        self.assertIn("## Safe Transaction Ledger", text)


if __name__ == "__main__":
    unittest.main()
