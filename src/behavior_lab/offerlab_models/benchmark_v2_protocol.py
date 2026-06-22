from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


class V2ProtocolError(ValueError):
    """Raised when Benchmark v2 protocol gates are not satisfied."""


@dataclass(frozen=True)
class HiddenExclusionReport:
    candidate_hidden_cases: int
    v1_exclusion_cases: int
    all_source_exclusion_proof: bool
    status: str


def validate_v2_hidden_exclusion(
    *,
    v2_manifest: dict[str, Any],
    v1_final_manifest: dict[str, Any],
    candidate_hidden_case_tokens: Iterable[str],
    external_v1_hidden_case_tokens: Iterable[str] | None = None,
    all_source_exclusion_proof: dict[str, Any] | None = None,
) -> HiddenExclusionReport:
    """Validate that a proposed v2 hidden set cannot reuse v1 hidden cases."""

    hidden_policy = v2_manifest.get("hidden_policy", {})
    if not hidden_policy.get("exclude_all_v1_hidden_case_tokens", False):
        raise V2ProtocolError("Benchmark v2 must require exclusion of all v1 hidden case tokens")

    candidate_tokens = {str(token) for token in candidate_hidden_case_tokens if str(token).strip()}
    if not candidate_tokens:
        raise V2ProtocolError("candidate hidden case token set is empty")

    manifest_token_block = (
        v1_final_manifest.get("hidden_lockbox", {})
        .get("case_tokens", {})
    )
    manifest_tokens = {
        str(token)
        for token in manifest_token_block.get("tokens", [])
        if str(token).strip()
    }
    external_tokens = {
        str(token)
        for token in (external_v1_hidden_case_tokens or [])
        if str(token).strip()
    }
    exclusion_tokens = manifest_tokens | external_tokens

    proof_ok = bool(
        all_source_exclusion_proof
        and all_source_exclusion_proof.get("proves_zero_overlap") is True
        and str(all_source_exclusion_proof.get("strategy_id", "")).strip()
        and str(all_source_exclusion_proof.get("artifact_hash", "")).strip()
    )
    if not exclusion_tokens and not proof_ok and hidden_policy.get("block_hidden_creation_if_v1_tokens_unavailable", False):
        raise V2ProtocolError(
            "v1 hidden exclusion tokens are unavailable; v2 hidden creation must remain blocked"
        )

    overlap = candidate_tokens & exclusion_tokens
    if overlap:
        raise V2ProtocolError(f"v2 hidden case set overlaps v1 hidden cases: {len(overlap)} token(s)")

    return HiddenExclusionReport(
        candidate_hidden_cases=len(candidate_tokens),
        v1_exclusion_cases=len(exclusion_tokens),
        all_source_exclusion_proof=proof_ok,
        status="ready",
    )
