from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from behavior_lab.core import HypothesisSpec, parse_time, stable_hash, utc_now
from behavior_lab.evaluation import evaluate_model, pareto_frontier
from behavior_lab.ledger import DuplicateRecordError, ImmutableLedger
from behavior_lab.models import LogisticFormulaHypothesis, ModelFoundry
from behavior_lab.temporal import assert_feature_map_is_pre_decision, split_rows, supervised_rows
from behavior_lab.worlds import HiddenWorld, make_world


TARGET = "started_within_10_minutes"
STANDARD_SPLITS = ("training", "development", "hidden", "prospective")
ALL_SPLITS = set(STANDARD_SPLITS) | {"staging"}


class EmptyEvaluationSplit(ValueError):
    pass


class CampaignExistsError(RuntimeError):
    pass


class SplitManifestError(RuntimeError):
    pass


class WorldConfigurationError(RuntimeError):
    pass


class BlindEvaluationServer:
    """Aggregate evaluation facade that redacts lockbox outcomes.

    This is a logical boundary inside one Python process, not a hostile-code
    sandbox.  Automated untrusted researchers must ultimately run out of process.
    """

    def __init__(
        self,
        splits: dict[str, list[dict[str, Any]]],
        target_name: str = TARGET,
        frozen_candidates: set[str] | None = None,
    ):
        self._splits = {key: list(value) for key, value in splits.items()}
        self.target_name = target_name
        self._frozen_candidates = set(frozen_candidates or set())

    def query_training_data(self, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is not None and limit < 0:
            raise ValueError("limit may not be negative")
        rows = self._splits.get("training", [])
        visible = rows[:limit] if limit is not None else rows
        return [
            {"case_id": row["case_id"], "features": dict(row["features"]), "target": row["target"]}
            for row in visible
        ]

    def evaluate(self, model: Any, split: str = "development") -> dict[str, Any]:
        if split not in self._splits:
            raise ValueError(f"Unknown split: {split}")
        rows = self._splits[split]
        if split != "training" and not rows:
            raise EmptyEvaluationSplit(f"Cannot evaluate an empty {split} split")
        if split == "prospective" and model.model_id not in self._frozen_candidates:
            raise PermissionError("Prospective evaluation requires a persistently frozen candidate")
        metrics = evaluate_model(model, rows, split=split, include_details=split == "development")
        payload = asdict(metrics)
        if split in {"hidden", "prospective"}:
            # The selected model may receive aggregate scoring, but the raw label
            # prevalence is not needed and creates avoidable lockbox leakage.
            payload.pop("base_rate", None)
            payload.pop("lift_over_base_log_loss", None)
            payload["details"] = {
                "redacted": (
                    "hidden labels, direct prevalence, baseline lift, and failure rows are not exposed; "
                    "any aggregate score still carries limited statistical information"
                )
            }
        return payload


class WorldGym:
    def __init__(
        self,
        data_dir: str | Path,
        world: HiddenWorld | None = None,
        target_name: str = TARGET,
        campaign_id: str = "default",
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = ImmutableLedger(self.data_dir / "ledger.jsonl")
        self.world = world or make_world("habit")
        self.target_name = target_name
        self.campaign_id = campaign_id
        self._validate_or_record_world_configuration()
        self._synchronize_world_index()

    def _validate_or_record_world_configuration(self) -> None:
        expected = {
            "world": self.world.name,
            "seed": int(self.world.seed),
            "subject_id": self.world.subject_id,
            "target_name": self.target_name,
        }
        existing = self.ledger.find_record("world_configuration", "world_configuration")
        if existing is None:
            self.ledger.append(
                "world_configuration",
                expected,
                record_id="world_configuration",
                unique_record_id=True,
            )
            return
        observed = existing.get("payload", {})
        if any(observed.get(key) != value for key, value in expected.items()):
            raise WorldConfigurationError(
                "Run directory belongs to a different world/seed/subject/target; use a clean directory"
            )

    def _synchronize_world_index(self) -> None:
        indices: list[int] = []
        for payload in self.ledger.payloads("decision_episode") + self.ledger.payloads("intervention_trial"):
            provenance = payload.get("data_provenance", {})
            try:
                indices.append(int(provenance.get("event_index", 0)))
            except (TypeError, ValueError):
                pass
        self.world.set_event_index(max(indices, default=0))

    def seed(self, episodes: int) -> int:
        if episodes < 0:
            raise ValueError("episodes may not be negative")
        generated = self.world.generate_dataset(episodes)
        entries = [("decision_episode", episode, episode.episode_id) for episode in generated]
        self.ledger.append_many_guarded(entries, unique_record_ids=True)
        return len(generated)

    def decision_episodes(self) -> list[dict[str, Any]]:
        return self.ledger.payloads("decision_episode")

    def decision_episode_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for record in self.ledger.scan("decision_episode"):
            payload = record["payload"]
            provenance = payload.get("data_provenance", {}).get("provenance", {})
            if provenance.get("collection_phase") == "pilot":
                continue
            row = supervised_rows([payload], self.target_name)
            if not row:
                continue
            materialized = row[0]
            # Episodes are ingested as a complete labeled record. Their ledger
            # ingestion is therefore also the earliest trustworthy eligibility
            # time for prospective classification.
            materialized["recorded_at"] = record["written_at"]
            materialized["eligibility_recorded_at"] = record["written_at"]
            rows.append(materialized)
        return rows

    def intervention_trial_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        assignments = {
            str(record.get("record_id")): record
            for record in self.ledger.scan("intervention_assignment")
        }
        for record in self.ledger.scan("intervention_trial"):
            trial = record["payload"]
            outcomes = trial.get("outcomes", {})
            if self.target_name not in outcomes and "started_within_10_minutes" not in outcomes:
                continue
            provenance = trial.get("data_provenance", {})
            features = provenance.get("intervened_context") or provenance.get("context_snapshot")
            if not isinstance(features, dict) or not features:
                continue
            assert_feature_map_is_pre_decision(features, target_name=self.target_name)
            target_value = outcomes.get(self.target_name, outcomes.get("started_within_10_minutes"))

            # A randomized trial's behavioral decision starts at assignment, not
            # when its outcome is later written.  Join the immutable assignment so
            # a pre-freeze assignment cannot masquerade as post-freeze evidence
            # merely because its outcome arrived later.
            assignment_record = assignments.get(str(trial.get("context_snapshot_id")))
            assignment_payload = assignment_record.get("payload", {}) if assignment_record else {}
            decision_time = (
                assignment_payload.get("assigned_at")
                or trial.get("recorded_at")
                or record["written_at"]
            )
            eligibility_recorded_at = (
                assignment_record.get("written_at") if assignment_record else record["written_at"]
            )
            parse_time(str(decision_time))
            parse_time(str(eligibility_recorded_at))
            rows.append(
                {
                    "case_id": trial["trial_id"],
                    "decision_time": decision_time,
                    "observation_cutoff": decision_time,
                    "features": dict(features, bias=1.0),
                    "target": 1 if target_value else 0,
                    "snapshot": {
                        "trial_id": trial["trial_id"],
                        "assignment_id": trial.get("context_snapshot_id"),
                        "pre_decision_context": dict(features),
                    },
                    "recorded_at": record["written_at"],
                    "eligibility_recorded_at": eligibility_recorded_at,
                }
            )
        return rows

    def rows(self) -> list[dict[str, Any]]:
        rows = self.decision_episode_rows()
        rows.extend(self.intervention_trial_rows())
        rows.sort(key=lambda item: (parse_time(item["decision_time"]), str(item["case_id"])))
        return rows

    def splits(self, campaign_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
        campaign = campaign_id or self.campaign_id
        rows = self.rows()
        assignments = self.ensure_split_manifest(rows, campaign_id=campaign)
        grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in STANDARD_SPLITS}
        for row in rows:
            split = assignments.get(row["case_id"])
            if split in grouped:
                grouped[split].append(row)
        for values in grouped.values():
            values.sort(key=lambda item: (parse_time(item["decision_time"]), str(item["case_id"])))
        return grouped

    def staging_rows(self, campaign_id: str | None = None) -> list[dict[str, Any]]:
        campaign = campaign_id or self.campaign_id
        rows = self.rows()
        assignments = self.ensure_split_manifest(rows, campaign_id=campaign)
        return [row for row in rows if assignments.get(row["case_id"]) == "staging"]

    def split_assignment_records(self, campaign_id: str | None = None) -> dict[str, dict[str, Any]]:
        campaign = campaign_id or self.campaign_id
        assignments: dict[str, dict[str, Any]] = {}
        for payload in self.ledger.payloads("split_assignment"):
            if str(payload.get("campaign_id", "default")) != campaign:
                continue
            case_id = payload.get("case_id") or payload.get("episode_id")
            split = payload.get("split")
            if not case_id or split not in ALL_SPLITS:
                raise SplitManifestError(f"Malformed split assignment in campaign {campaign!r}: {payload!r}")
            prior = assignments.get(str(case_id))
            if prior is not None:
                raise SplitManifestError(
                    f"Case {case_id!r} has multiple split assignments in campaign {campaign!r}; "
                    "split and freeze bindings are immutable"
                )
            assignments[str(case_id)] = payload
        return assignments

    def split_assignments(self, campaign_id: str | None = None) -> dict[str, str]:
        return {
            case_id: str(payload["split"])
            for case_id, payload in self.split_assignment_records(campaign_id).items()
        }

    def campaign_exists(self, campaign_id: str) -> bool:
        return self.ledger.find_record(f"campaign_{campaign_id}", "campaign_start") is not None

    def create_campaign(self, campaign_id: str, rows: list[dict[str, Any]] | None = None) -> dict[str, str]:
        if not campaign_id.strip():
            raise ValueError("campaign_id must be non-empty")
        if self.campaign_exists(campaign_id):
            raise CampaignExistsError(f"Campaign {campaign_id!r} already exists")
        rows = list(rows if rows is not None else self.rows())
        initial = split_rows(rows, train_fraction=0.6, development_fraction=0.2, hidden_fraction=0.2)
        assignments: dict[str, str] = {}
        entries: list[tuple[str, Any, str | None]] = [
            (
                "campaign_start",
                {
                    "campaign_id": campaign_id,
                    "started_at": utc_now(),
                    "available_cases": len(rows),
                    "policy_version": "chronological_manifest_v4",
                },
                f"campaign_{campaign_id}",
            )
        ]
        assigned_at = utc_now()
        for split, selected_rows in initial.items():
            for row in selected_rows:
                case_id = str(row["case_id"])
                assignments[case_id] = split
                entries.append(
                    (
                        "split_assignment",
                        self._split_payload(
                            case_id,
                            split,
                            "chronological_manifest_v4",
                            campaign_id,
                            assigned_at=assigned_at,
                            decision_time=str(row["decision_time"]),
                            recorded_at=str(row.get("recorded_at", row["decision_time"])),
                            eligibility_recorded_at=str(
                                row.get("eligibility_recorded_at", row.get("recorded_at", row["decision_time"]))
                            ),
                        ),
                        f"split_{campaign_id}_{case_id}",
                    )
                )
        self.ledger.append_many_guarded(entries, unique_record_ids=True)
        return assignments

    def ensure_split_manifest(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        campaign_id: str | None = None,
    ) -> dict[str, str]:
        """Assign each case once within a campaign.

        New pre-freeze observations are staging and can only enter model fitting in
        a new campaign. A post-freeze record is prospective only when both its
        decision time is later than the frozen data cutoff and its ledger ingestion
        time is later than the freeze. Delayed historical backfills remain staging.
        """

        campaign = campaign_id or self.campaign_id
        rows = list(rows if rows is not None else self.rows())
        assignment_records = self.split_assignment_records(campaign)
        if not self.campaign_exists(campaign):
            return self.create_campaign(campaign, rows)

        missing = [row for row in rows if str(row["case_id"]) not in assignment_records]
        if not missing:
            return {case_id: str(payload["split"]) for case_id, payload in assignment_records.items()}

        freeze = self.latest_freeze(campaign)
        assigned_at = utc_now()
        entries = []
        for row in missing:
            case_id = str(row["case_id"])
            decision_time = str(row["decision_time"])
            recorded_at = str(row.get("recorded_at", decision_time))
            eligibility_recorded_at = str(
                row.get("eligibility_recorded_at", recorded_at)
            )
            split = "staging"
            policy = "pre_freeze_staging_v4"
            freeze_id: str | None = None
            if freeze is not None:
                cutoff = parse_time(str(freeze["data_cutoff_time"]))
                frozen_at = parse_time(str(freeze["frozen_at"]))
                occurred_after_cutoff = parse_time(decision_time) > cutoff
                ingested_after_freeze = parse_time(eligibility_recorded_at) > frozen_at
                if occurred_after_cutoff and ingested_after_freeze:
                    split = "prospective"
                    policy = "post_freeze_prospective_v4"
                    freeze_id = str(freeze["freeze_id"])
                else:
                    policy = "post_freeze_backfill_staging_v4"
            entries.append(
                (
                    "split_assignment",
                    self._split_payload(
                        case_id,
                        split,
                        policy,
                        campaign,
                        assigned_at=assigned_at,
                        decision_time=decision_time,
                        recorded_at=recorded_at,
                        eligibility_recorded_at=eligibility_recorded_at,
                        freeze_id=freeze_id,
                    ),
                    f"split_{campaign}_{case_id}",
                )
            )
        try:
            self.ledger.append_many_guarded(entries, unique_record_ids=True)
        except DuplicateRecordError:
            # A concurrent process may have assigned them. Re-read and validate.
            pass
        return self.split_assignments(campaign)

    def _split_payload(
        self,
        case_id: str,
        split: str,
        policy_version: str,
        campaign_id: str,
        *,
        assigned_at: str,
        decision_time: str | None = None,
        recorded_at: str | None = None,
        eligibility_recorded_at: str | None = None,
        freeze_id: str | None = None,
    ) -> dict[str, Any]:
        if split not in ALL_SPLITS:
            raise ValueError(f"Unknown split assignment: {split}")
        payload: dict[str, Any] = {
            "episode_id": case_id,
            "case_id": case_id,
            "split": split,
            "campaign_id": campaign_id,
            "assigned_at": assigned_at,
            "split_policy_version": policy_version,
        }
        if decision_time is not None:
            parse_time(decision_time)
            payload["decision_time"] = decision_time
        if recorded_at is not None:
            parse_time(recorded_at)
            payload["recorded_at"] = recorded_at
        if eligibility_recorded_at is not None:
            parse_time(eligibility_recorded_at)
            payload["eligibility_recorded_at"] = eligibility_recorded_at
        if freeze_id is not None:
            payload["freeze_id"] = freeze_id
        return payload

    def blind_server(self, campaign_id: str | None = None) -> BlindEvaluationServer:
        campaign = campaign_id or self.campaign_id
        return BlindEvaluationServer(self.splits(campaign), self.target_name, self.frozen_model_ids(campaign))

    def latest_freeze(self, campaign_id: str | None = None) -> dict[str, Any] | None:
        campaign = campaign_id or self.campaign_id
        match = None
        for payload in self.ledger.payloads("frozen_candidate"):
            if payload.get("campaign_id", "default") == campaign:
                match = payload
        return match

    def frozen_model_ids(self, campaign_id: str | None = None) -> set[str]:
        campaign = campaign_id or self.campaign_id
        return {
            str(payload["model_id"])
            for payload in self.ledger.payloads("frozen_candidate")
            if "model_id" in payload and payload.get("campaign_id", "default") == campaign
        }

    def split_snapshot_hash(self, split: str, campaign_id: str | None = None) -> str:
        if split not in STANDARD_SPLITS:
            raise ValueError(f"Unknown split: {split}")
        campaign = campaign_id or self.campaign_id
        case_ids = sorted(row["case_id"] for row in self.splits(campaign)[split])
        return stable_hash({"split": split, "case_ids": case_ids})

    def prospective_rows_for_freeze(self, freeze_id: str, campaign_id: str | None = None) -> list[dict[str, Any]]:
        campaign = campaign_id or self.campaign_id
        freeze = next(
            (
                payload
                for payload in self.ledger.payloads("frozen_candidate")
                if payload.get("campaign_id", "default") == campaign
                and payload.get("freeze_id") == freeze_id
            ),
            None,
        )
        if freeze is None:
            raise SplitManifestError(f"Unknown freeze {freeze_id!r} in campaign {campaign!r}")
        cutoff = parse_time(str(freeze["data_cutoff_time"]))
        frozen_at = parse_time(str(freeze["frozen_at"]))
        rows_by_id = {str(row["case_id"]): row for row in self.rows()}
        selected: list[dict[str, Any]] = []
        for case_id, payload in self.split_assignment_records(campaign).items():
            if payload.get("split") != "prospective" or payload.get("freeze_id") != freeze_id:
                continue
            row = rows_by_id.get(case_id)
            if row is None:
                continue
            if parse_time(str(row["decision_time"])) <= cutoff:
                raise SplitManifestError(
                    f"Prospective case {case_id!r} occurred on or before the freeze data cutoff"
                )
            if parse_time(
                str(row.get("eligibility_recorded_at", row.get("recorded_at", row["decision_time"])))
            ) <= frozen_at:
                raise SplitManifestError(
                    f"Prospective case {case_id!r} became decision-eligible on or before the model freeze"
                )
            selected.append(row)
        selected.sort(key=lambda row: (parse_time(row["decision_time"]), str(row["case_id"])))
        return selected

    def fit_hypothesis(self, spec: HypothesisSpec, campaign_id: str | None = None) -> Any:
        return LogisticFormulaHypothesis(spec).fit(self.splits(campaign_id)["training"])

    def fit_model_zoo(self, campaign_id: str | None = None) -> list[Any]:
        splits = self.splits(campaign_id)
        return ModelFoundry().fit_zoo(splits["training"], splits["development"], self.target_name)

    def leaderboard(self, split: str = "development", campaign_id: str | None = None) -> list[dict[str, Any]]:
        if split in {"hidden", "prospective"}:
            raise PermissionError("Use ResearchAPI lockboxes for hidden/prospective evaluation")
        campaign = campaign_id or self.campaign_id
        server = self.blind_server(campaign)
        results = [server.evaluate(model, split=split) for model in self.fit_model_zoo(campaign)]
        results.sort(key=lambda item: item["log_loss"])
        return results

    def complexity_frontier(self, campaign_id: str | None = None) -> list[dict[str, Any]]:
        campaign = campaign_id or self.campaign_id
        splits = self.splits(campaign)
        metrics = [
            evaluate_model(model, splits["development"], split="development")
            for model in self.fit_model_zoo(campaign)
        ]
        return pareto_frontier(metrics)
