from __future__ import annotations

from dataclasses import asdict
from typing import Any

from behavior_lab.core import EvaluationMetrics, FittedHypothesisRecord, HypothesisSpec, new_id, parse_time, stable_hash, to_jsonable, utc_now
from behavior_lab.ledger import ImmutableLedger
from behavior_lab.models import validate_model_artifact


class EvaluationBudgetError(RuntimeError):
    pass


class RegistryConflictError(RuntimeError):
    pass


class ModelRegistry:
    def __init__(self, ledger: ImmutableLedger):
        self.ledger = ledger

    def submit_hypothesis(self, spec: HypothesisSpec) -> dict[str, Any]:
        payload = asdict(spec)
        existing = self.ledger.find_record(spec.hypothesis_id, "hypothesis")
        if existing is not None:
            if stable_hash(existing["payload"]) != stable_hash(payload):
                raise RegistryConflictError(
                    f"Hypothesis ID {spec.hypothesis_id!r} already exists with different content"
                )
            return existing
        return self.ledger.append(
            "hypothesis",
            payload,
            record_id=spec.hypothesis_id,
            unique_record_id=True,
        )

    def record_fit(
        self,
        model: Any,
        hypothesis_id: str,
        training_split: str,
        training_cases: int,
        artifact: dict[str, Any] | None = None,
        *,
        campaign_id: str = "default",
    ) -> dict[str, Any]:
        if artifact is None:
            raise RegistryConflictError(
                "A persisted model fit requires a reloadable hashed artifact"
            )
        validate_model_artifact(artifact)
        if artifact.get("model_id") != model.model_id:
            raise RegistryConflictError("Model artifact ID does not match the fitted model")
        parameters = getattr(model, "parameters", {})
        record = FittedHypothesisRecord(
            model_id=model.model_id,
            hypothesis_id=hypothesis_id,
            fitted_at=utc_now(),
            training_split=training_split,
            training_cases=training_cases,
            parameters=parameters,
            artifact=artifact,
            campaign_id=campaign_id,
        )
        payload = asdict(record)

        def guard(records: list[dict[str, Any]]) -> None:
            for existing in records:
                if existing.get("record_type") != "model_fit":
                    continue
                prior = existing.get("payload", {})
                if prior.get("campaign_id", "default") != campaign_id:
                    continue
                if prior.get("model_id") != model.model_id:
                    continue
                prior_hash = prior.get("artifact", {}).get("artifact_hash")
                if (
                    prior_hash != artifact.get("artifact_hash")
                    or prior.get("hypothesis_id") != hypothesis_id
                    or prior.get("training_split") != training_split
                ):
                    raise RegistryConflictError(
                        f"Model ID {model.model_id!r} already identifies a different fit in "
                        f"campaign {campaign_id!r}"
                    )
                raise RegistryConflictError(
                    f"Model fit {model.model_id!r} is already persisted in campaign {campaign_id!r}"
                )

        return self.ledger.append_guarded(
            "model_fit",
            payload,
            record_id=new_id("fit"),
            guard=guard,
        )

    def latest_fit(self, model_id: str, campaign_id: str | None = None) -> dict[str, Any] | None:
        match = None
        for payload in self.ledger.payloads("model_fit"):
            if payload.get("model_id") != model_id:
                continue
            if campaign_id is not None and payload.get("campaign_id", "default") != campaign_id:
                continue
            match = payload
        return match

    def record_evaluation(self, metrics: EvaluationMetrics, *, campaign_id: str = "default") -> dict[str, Any]:
        payload = asdict(metrics)
        payload["campaign_id"] = campaign_id
        payload["evaluated_at"] = utc_now()
        return self.ledger.append("evaluation", payload, record_id=new_id("eval"))

    def record_evaluation_from_payload(
        self,
        payload: dict[str, Any],
        *,
        campaign_id: str = "default",
        budget_use_id: str | None = None,
    ) -> dict[str, Any]:
        body = dict(payload)
        body["campaign_id"] = campaign_id
        body["evaluated_at"] = utc_now()
        if budget_use_id is not None:
            body["budget_use_id"] = budget_use_id
        return self.ledger.append("evaluation", body, record_id=new_id("eval"))

    def reserve_evaluation_budget(
        self,
        *,
        campaign_id: str,
        model_id: str,
        split: str,
        scope_id: str,
        case_ids: list[str],
        limit: int = 1,
        artifact_hash: str | None = None,
        freeze_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically consume a lockbox query budget.

        `scope_id` is derived from the actual holdout/freeze, not a caller-picked
        campaign name.  Hidden case reuse is also rejected across campaigns so a
        researcher cannot repeatedly probe almost the same holdout by renaming it.
        """

        if split not in {"hidden", "prospective"}:
            raise ValueError("Evaluation budgets apply only to hidden and prospective splits")
        if limit <= 0:
            raise EvaluationBudgetError(f"Evaluation budget for {split} is zero")
        if not scope_id.strip():
            raise ValueError("scope_id must be non-empty")
        if not artifact_hash or not freeze_id:
            raise EvaluationBudgetError(
                f"{split} lockbox reservations require an exact frozen artifact and freeze ID"
            )
        normalized_case_ids = sorted({str(case_id) for case_id in case_ids})
        if not normalized_case_ids:
            raise EvaluationBudgetError(f"Cannot reserve an empty {split} lockbox")
        budget_id = new_id("budget")
        payload = {
            "budget_use_id": budget_id,
            "campaign_id": campaign_id,
            "model_id": model_id,
            "split": split,
            "scope_id": scope_id,
            "case_ids": normalized_case_ids,
            "case_ids_hash": stable_hash(normalized_case_ids),
            "limit": limit,
            "artifact_hash": artifact_hash,
            "freeze_id": freeze_id,
            "status": "reserved",
            "used_at": utc_now(),
        }

        def guard(records: list[dict[str, Any]]) -> None:
            prior_uses = [
                record.get("payload", {})
                for record in records
                if record.get("record_type") == "evaluation_budget_use"
                and record.get("payload", {}).get("split") == split
            ]
            same_scope = [item for item in prior_uses if item.get("scope_id") == scope_id]
            if len(same_scope) >= limit:
                raise EvaluationBudgetError(
                    f"Evaluation budget exhausted for {split!r} scope {scope_id!r}; limit is {limit}"
                )
            if split == "hidden":
                current = set(normalized_case_ids)
                for item in prior_uses:
                    overlap = current.intersection(str(value) for value in item.get("case_ids", []))
                    if overlap:
                        raise EvaluationBudgetError(
                            "Hidden lockbox reuses cases exposed by an earlier aggregate query; "
                            "start a fresh dataset instead of changing campaign IDs"
                        )

        return self.ledger.append_guarded(
            "evaluation_budget_use",
            payload,
            record_id=budget_id,
            unique_record_id=True,
            guard=guard,
        )

    def assert_evaluation_budget_available(
        self,
        *,
        split: str,
        scope_id: str,
        limit: int = 1,
    ) -> None:
        uses = [
            payload
            for payload in self.ledger.payloads("evaluation_budget_use")
            if payload.get("split") == split and payload.get("scope_id") == scope_id
        ]
        if len(uses) >= limit:
            raise EvaluationBudgetError(
                f"Evaluation budget exhausted for {split!r} scope {scope_id!r}; limit is {limit}"
            )

    def freeze_candidate(
        self,
        model_id: str,
        split: str,
        reason: str,
        *,
        campaign_id: str = "default",
        split_snapshot_hash: str,
        data_cutoff_time: str,
    ) -> dict[str, Any]:
        if split != "prospective":
            raise ValueError("Candidates are frozen specifically for prospective evaluation")
        parse_time(data_cutoff_time)
        fit = self.latest_fit(model_id, campaign_id)
        if fit is None:
            raise RegistryConflictError(
                f"Cannot freeze {model_id!r}: no persisted fit exists in campaign {campaign_id!r}"
            )
        artifact = fit.get("artifact", {})
        artifact_hash = artifact.get("artifact_hash")
        if not artifact_hash:
            raise RegistryConflictError("Cannot freeze a model without a hashed, reloadable artifact")
        training_snapshot_hash = artifact.get("training_snapshot_hash")
        if not training_snapshot_hash:
            raise RegistryConflictError("Cannot freeze a model without a training snapshot hash")

        existing = self.frozen_candidate_for_campaign(campaign_id)
        if existing is not None:
            if existing.get("model_id") != model_id:
                raise RegistryConflictError(
                    f"Campaign {campaign_id!r} already froze model {existing.get('model_id')!r}"
                )
            return {"record_type": "frozen_candidate", "payload": existing, "record_id": existing["freeze_id"]}

        # Retry if an observation arrives between taking the ledger head and the
        # guarded append.  The guard also requires every current case to have a
        # campaign assignment, closing the pre-freeze/unassigned race.
        for _ in range(3):
            expected_head = self.ledger.last_hash()
            freeze_id = new_id("freeze")
            payload = {
                "freeze_id": freeze_id,
                "model_id": model_id,
                "split": split,
                "frozen_at": utc_now(),
                "reason": reason,
                "campaign_id": campaign_id,
                "artifact_hash": artifact_hash,
                "training_snapshot_hash": training_snapshot_hash,
                "split_snapshot_hash": split_snapshot_hash,
                "data_cutoff_time": data_cutoff_time,
                "ledger_head_before_freeze": expected_head,
            }

            def guard(records: list[dict[str, Any]]) -> None:
                current_head = str(records[-1]["record_hash"]) if records else self.ledger.genesis_hash
                if current_head != expected_head:
                    raise RegistryConflictError("Ledger changed while candidate was being frozen; retry")
                freezes = [
                    record.get("payload", {})
                    for record in records
                    if record.get("record_type") == "frozen_candidate"
                    and record.get("payload", {}).get("campaign_id", "default") == campaign_id
                ]
                if freezes:
                    existing_model = freezes[-1].get("model_id")
                    if existing_model != model_id:
                        raise RegistryConflictError(
                            f"Campaign {campaign_id!r} already froze model {existing_model!r}"
                        )
                    raise RegistryConflictError("Candidate was concurrently frozen; reload the registry")
                case_ids = {
                    str(record.get("payload", {}).get("episode_id"))
                    for record in records
                    if record.get("record_type") == "decision_episode"
                }
                case_ids.update(
                    str(record.get("payload", {}).get("trial_id"))
                    for record in records
                    if record.get("record_type") == "intervention_trial"
                )
                assigned = {
                    str(record.get("payload", {}).get("case_id") or record.get("payload", {}).get("episode_id"))
                    for record in records
                    if record.get("record_type") == "split_assignment"
                    and record.get("payload", {}).get("campaign_id", "default") == campaign_id
                }
                missing = {case_id for case_id in case_ids if case_id and case_id != "None"} - assigned
                if missing:
                    raise RegistryConflictError(
                        "Unassigned observations exist at freeze time; refresh the split manifest before freezing"
                    )

            try:
                return self.ledger.append_guarded(
                    "frozen_candidate",
                    payload,
                    record_id=freeze_id,
                    unique_record_id=True,
                    guard=guard,
                )
            except RegistryConflictError as exc:
                if "Ledger changed" in str(exc):
                    continue
                raise
        raise RegistryConflictError("Could not obtain a stable ledger cut for candidate freeze")

    def frozen_candidate_for_campaign(self, campaign_id: str = "default") -> dict[str, Any] | None:
        match = None
        for payload in self.ledger.payloads("frozen_candidate"):
            if payload.get("campaign_id", "default") == campaign_id:
                match = payload
        return match

    def frozen_candidate(self, model_id: str, campaign_id: str = "default") -> dict[str, Any] | None:
        candidate = self.frozen_candidate_for_campaign(campaign_id)
        if candidate is not None and candidate.get("model_id") == model_id:
            return candidate
        return None

    def promote_hypothesis(
        self, hypothesis_id: str, model_id: str, reason: str, *, campaign_id: str = "default"
    ) -> dict[str, Any]:
        return self.ledger.append(
            "hypothesis_status",
            {
                "hypothesis_id": hypothesis_id,
                "model_id": model_id,
                "status": "promoted",
                "reason": reason,
                "campaign_id": campaign_id,
                "written_at": utc_now(),
            },
        )

    def retire_hypothesis(
        self,
        hypothesis_id: str,
        reason: str,
        evidence: dict[str, Any] | None = None,
        *,
        campaign_id: str = "default",
    ) -> dict[str, Any]:
        return self.ledger.append(
            "hypothesis_status",
            {
                "hypothesis_id": hypothesis_id,
                "status": "retired",
                "reason": reason,
                "evidence": evidence or {},
                "campaign_id": campaign_id,
                "written_at": utc_now(),
            },
        )

    def model_obituary(
        self, hypothesis_id: str, body: str, evidence: dict[str, Any], *, campaign_id: str = "default"
    ) -> dict[str, Any]:
        return self.ledger.append(
            "model_obituary",
            {
                "hypothesis_id": hypothesis_id,
                "body": body,
                "evidence": evidence,
                "campaign_id": campaign_id,
                "written_at": utc_now(),
            },
        )

    def inspect_model_registry(self, campaign_id: str | None = None) -> dict[str, Any]:
        def filtered(record_type: str) -> list[dict[str, Any]]:
            payloads = self.ledger.payloads(record_type)
            if campaign_id is None:
                return payloads
            return [payload for payload in payloads if payload.get("campaign_id", "default") == campaign_id]

        return {
            "hypotheses": self.ledger.payloads("hypothesis"),
            "fits": filtered("model_fit"),
            "evaluations": filtered("evaluation"),
            "evaluation_budget_uses": self.ledger.payloads("evaluation_budget_use"),
            "status_events": filtered("hypothesis_status"),
            "frozen_candidates": filtered("frozen_candidate"),
        }

    def lineage_graph(self) -> dict[str, Any]:
        nodes: dict[str, Any] = {}
        edges: list[dict[str, str]] = []
        for hypothesis in self.ledger.payloads("hypothesis"):
            hypothesis_id = hypothesis["hypothesis_id"]
            nodes[hypothesis_id] = hypothesis
            for parent in hypothesis.get("parent_ids", []):
                edges.append({"from": parent, "to": hypothesis_id, "kind": "parent"})
        for status in self.ledger.payloads("hypothesis_status"):
            nodes.setdefault(status["hypothesis_id"], {})["latest_status"] = status["status"]
        return {"nodes": to_jsonable(nodes), "edges": edges}
