from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
HANDOFF_JSON = ROOT / "reports" / "finance" / "financial_pivot_handoff.json"
HANDOFF_MD = ROOT / "docs" / "finance" / "FINANCIAL_PIVOT_HANDOFF.md"
ALLOWED_EVIDENCE_CLASSES = {
    "public_research_evidence",
    "private_seller_historical_evidence",
    "prospective_shadow_evidence",
    "realized_commercial_evidence",
}


def _payload() -> dict:
    return json.loads(HANDOFF_JSON.read_text(encoding="utf-8"))


def test_financial_pivot_handoff_artifacts_exist_and_use_allowed_evidence_classes() -> None:
    assert HANDOFF_JSON.exists()
    assert HANDOFF_MD.exists()
    payload = _payload()

    assert set(payload["allowed_evidence_classes"]) == ALLOWED_EVIDENCE_CLASSES
    assert payload["evidence_inventory"]
    for result in payload["evidence_inventory"]:
        assert result["evidence_class"] in ALLOWED_EVIDENCE_CLASSES
        assert result["evidence_class"] in set(payload["allowed_evidence_classes"])


def test_public_research_evidence_cannot_be_reported_as_commercial() -> None:
    payload = _payload()
    by_id = {result["result_id"]: result for result in payload["evidence_inventory"]}

    assert by_id["offerlab_benchmark_v1"]["evidence_class"] == "public_research_evidence"
    assert by_id["offerlab_benchmark_v2"]["evidence_class"] == "public_research_evidence"
    assert by_id["offerlab_benchmark_v1"]["commercial_use_allowed"] is False
    assert by_id["offerlab_benchmark_v2"]["commercial_use_allowed"] is False
    assert by_id["offerlab_benchmark_v1"]["causal_profit_claim_allowed"] is False
    assert by_id["offerlab_benchmark_v2"]["causal_profit_claim_allowed"] is False
    assert payload["evidence_class_policy"]["realized_commercial_evidence_present"] is False
    assert payload["benchmark_v1_final_status"]["hidden_reuse_allowed"] is False


def test_handoff_preserves_manual_only_financial_boundary() -> None:
    payload = _payload()
    policy = payload["real_action_policy"]

    assert policy["manual_only"] is True
    assert policy["paper_decisions_allowed"] is True
    assert policy["submits_trades"] is False
    assert policy["submits_seller_actions"] is False
    assert policy["purchases_inventory"] is False
    assert policy["sends_notifications"] is False
    assert "trade execution" in payload["supportable_targets_and_actions"]["not_supportable_now"]
    assert "automated seller actions" in payload["supportable_targets_and_actions"]["not_supportable_now"]


def test_handoff_does_not_publish_local_or_private_paths() -> None:
    rendered = json.dumps(_payload(), sort_keys=True) + HANDOFF_MD.read_text(encoding="utf-8")

    assert "C:\\" not in rendered
    assert "\\Users\\" not in rendered
    assert "seller_pilots" not in rendered


def test_subsequent_wave_base_commit_contains_handoff_artifacts() -> None:
    payload = _payload()
    base_resolution = payload["exact_base_commit_for_subsequent_waves"]

    assert base_resolution["status"] == "resolved_by_independent_pass_audit"
    assert base_resolution["audit_wave_id"] == "FINANCE_WAVE_0"
    assert "HEAD_COMMIT" in base_resolution["resolution_rule"]
    base = base_resolution["minimum_handoff_artifact_commit"]

    for path in (
        "docs/finance/FINANCIAL_PIVOT_HANDOFF.md",
        "reports/finance/financial_pivot_handoff.json",
    ):
        subprocess.run(
            ["git", "cat-file", "-e", f"{base}:{path}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
