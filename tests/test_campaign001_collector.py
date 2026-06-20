from __future__ import annotations

import _bootstrap  # noqa: F401

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

from behavior_lab.bridge import source_hash_for_snapshot, validate_snapshot_file
from behavior_lab.campaign001_collector import (
    AUDIT_RECORD_TYPE,
    CollectorError,
    amend_capture,
    atomic_write_json,
    finalize_capture,
    invalidate_capture,
    missed_capture,
    recover_atomic_writes,
    resume_capture,
    start_capture,
    status_capture,
)
from behavior_lab.gym import WorldGym
from behavior_lab.ledger import ImmutableLedger


def _start_script(**overrides: object) -> dict:
    script = {
        "episode_uuid": "11111111-1111-1111-1111-111111111111",
        "decision_time": "2026-06-20T09:00:00-04:00",
        "observation_cutoff": "2026-06-20T09:00:00-04:00",
        "timezone": "America/New_York",
        "task_description": "Open experiment_service.py and implement run_trial",
        "task_type": "coding",
        "time_of_day": "morning",
        "fatigue": 1,
        "ambiguity": 1,
        "estimated_minutes": 45,
        "first_step_explicit": True,
        "has_deadline": True,
        "deadline_hours": 24,
        "recent_context_switches": 2,
        "public_commitment": False,
        "manual_note": "kept in provenance only",
    }
    script.update(overrides)
    return script


def _outcome_script(**overrides: object) -> dict:
    script = {
        "started_within_10_minutes": True,
        "start_latency_seconds": 180,
        "worked_for_20_minutes": True,
        "completed_that_day": False,
        "outcome_sources": {
            "started_within_10_minutes": "timer_assisted",
            "start_latency_seconds": "timer_assisted",
            "worked_for_20_minutes": "manual_observation",
            "completed_that_day": "manual_observation",
        },
        "recorded_at": "2026-06-20T23:00:00-04:00",
        "monotonic_end": 2000.0,
    }
    script.update(overrides)
    return script


def _read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class Campaign001CollectorTests(unittest.TestCase):
    def test_start_seals_predecision_without_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = start_capture(tmp, script=_start_script())
            artifact = _read_json(result["artifact_path"])
            self.assertTrue(result["pre_decision_valid"])
            self.assertNotIn("protected_outcome", artifact)
            self.assertIn("pre_decision_hash", artifact)
            self.assertEqual(artifact["event_log"][0]["event"], "pre_decision_sealed")
            self.assertNotIn("manual_note", artifact["sealed_pre_decision_snapshot"]["pre_decision_features"])
            self.assertEqual(artifact["sealed_pre_decision_snapshot"]["provenance"]["manual_note"], "kept in provenance only")

    def test_start_rejects_outcome_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CollectorError):
                start_capture(tmp, script=_start_script(started_within_10_minutes=True))
            with self.assertRaises(CollectorError):
                start_capture(tmp, script=_start_script(protected_outcome={"started_within_10_minutes": True}))

    def test_predecision_hash_changes_when_field_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = start_capture(Path(tmp) / "a", script=_start_script(episode_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
            second = start_capture(
                Path(tmp) / "b",
                script=_start_script(episode_uuid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", fatigue=2),
            )
            self.assertNotEqual(first["pre_decision_hash"], second["pre_decision_hash"])

    def test_finalize_exports_imports_and_verifies_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            start = start_capture(tmp, script=_start_script())
            final = finalize_capture(start["episode_id"], tmp, script=_outcome_script())
            self.assertEqual(final["ledger_record_id"], start["episode_id"])
            self.assertTrue(final["ledger_valid"])
            self.assertTrue(final["imported"])
            ledger = ImmutableLedger(Path(tmp) / "ledger.jsonl")
            self.assertTrue(ledger.verify_hash_chain())
            self.assertEqual(len(ledger.payloads("decision_episode")), 1)
            self.assertEqual(validate_snapshot_file(final["bridge_export_path"])["snapshots"], 1)

    def test_finalize_retry_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            start = start_capture(tmp, script=_start_script())
            finalize_capture(start["episode_id"], tmp, script=_outcome_script())
            second = finalize_capture(start["episode_id"], tmp, script=_outcome_script())
            ledger = ImmutableLedger(Path(tmp) / "ledger.jsonl")
            self.assertTrue(second["already_imported"])
            self.assertEqual(len(ledger.payloads("decision_episode")), 1)

    def test_resume_after_seal_can_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            start = start_capture(tmp, script=_start_script())
            resumable = resume_capture(tmp)
            self.assertEqual(resumable["resumable_episodes"][0]["episode_id"], start["episode_id"])
            final = resume_capture(tmp, episode_id=start["episode_id"], script=_outcome_script())
            self.assertEqual(final["episode_status"], "completed")

    def test_no_deadline_is_explicit_and_can_be_finalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            start = start_capture(tmp, script=_start_script(has_deadline=False, deadline_hours=None))
            final = finalize_capture(start["episode_id"], tmp, script=_outcome_script())
            artifact = _read_json(start["artifact_path"])
            self.assertIsNone(artifact["sealed_pre_decision_snapshot"]["pre_decision_features"]["deadline_hours"])
            self.assertFalse(artifact["sealed_pre_decision_snapshot"]["pre_decision_features"]["has_deadline"])
            self.assertEqual(final["episode_status"], "completed")

    def test_missing_required_fields_are_explicit_and_reach_audit_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            start = start_capture(tmp, script=_start_script(task_type=None))
            artifact = _read_json(start["artifact_path"])
            ledger = ImmutableLedger(Path(tmp) / "ledger.jsonl")
            audit = ledger.payloads(AUDIT_RECORD_TYPE)
            self.assertEqual(start["episode_status"], "incomplete")
            self.assertIn("task_type", start["missing_fields"])
            self.assertIsNone(artifact["sealed_pre_decision_snapshot"]["pre_decision_features"]["task_type"])
            self.assertEqual(len(audit), 1)
            self.assertEqual(audit[0]["episode_status"], "incomplete")
            self.assertEqual(audit[0]["audit_event"], "incomplete_pre_decision")
            with self.assertRaises(CollectorError):
                finalize_capture(start["episode_id"], tmp, script=_outcome_script())

    def test_amendment_preserves_original_sealed_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            start = start_capture(tmp, script=_start_script())
            artifact_before = _read_json(start["artifact_path"])
            amend = amend_capture(start["episode_id"], "fatigue", 2, "typed the wrong value", tmp)
            artifact_after = _read_json(amend["artifact_path"])
            self.assertEqual(artifact_before["sealed_pre_decision_snapshot"], artifact_after["sealed_pre_decision_snapshot"])
            self.assertEqual(artifact_after["amendments"][0]["original_value"], 1)
            self.assertEqual(artifact_after["amendments"][0]["corrected_value"], 2)

    def test_missed_capture_records_bias_without_completed_episode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missed = missed_capture(
                tmp,
                script={
                    "missed_uuid": "99999999-9999-9999-9999-999999999999",
                    "occurred_at": "2026-06-20T10:00:00-04:00",
                    "task_description": "Task decision happened before capture",
                    "reason": "forgot to open collector",
                },
            )
            artifact = _read_json(missed["artifact_path"])
            status = status_capture(tmp)
            ledger = ImmutableLedger(Path(tmp) / "ledger.jsonl")
            self.assertEqual(artifact["capture_status"], "missed_pre_decision")
            self.assertEqual(len(ledger.payloads(AUDIT_RECORD_TYPE)), 1)
            self.assertIn("audit_ledger_record_id", missed)
            self.assertEqual(status["missed_eligible_episode_count"], 1)
            self.assertEqual(status["completed_natural_episode_count"], 0)

    def test_status_is_operational_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            start_capture(tmp, script=_start_script())
            text = json.dumps(status_capture(tmp), sort_keys=True).lower()
            forbidden = ["base_rate", "log_loss", "calibration", "prediction", "hypothesis", "model"]
            for word in forbidden:
                self.assertNotIn(word, text)

    def test_status_counts_incomplete_invalidated_and_excludes_interventions_from_natural_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = start_capture(tmp, script=_start_script(episode_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
            finalize_capture(completed["episode_id"], tmp, script=_outcome_script())
            incomplete = start_capture(
                tmp,
                script=_start_script(episode_uuid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", task_type=None),
            )
            invalid = start_capture(tmp, script=_start_script(episode_uuid="cccccccc-cccc-cccc-cccc-cccccccccccc"))
            invalidate_capture(invalid["episode_id"], "boundary ambiguous", tmp)

            intervention_artifact = _read_json(completed["artifact_path"])
            intervention_artifact["episode_id"] = "c001_intervention_example"
            intervention_artifact["episode_status"] = "completed"
            intervention_artifact["sealed_pre_decision_snapshot"]["provenance"]["collection_mode"] = "randomized_intervention"
            atomic_write_json(Path(tmp) / "captures" / "c001_intervention_example.json", intervention_artifact)

            status = status_capture(tmp)
            self.assertEqual(status["completed_natural_episode_count"], 1)
            self.assertEqual(status["episode_status_counts"]["completed"], 2)
            self.assertEqual(status["episode_status_counts"]["incomplete"], 1)
            self.assertEqual(status["episode_status_counts"]["invalidated"], 1)
            self.assertGreaterEqual(status["audit_records"], 2)
            self.assertEqual(incomplete["episode_status"], "incomplete")

    def test_bridge_export_keeps_actions_and_protected_outcome_separate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            start = start_capture(tmp, script=_start_script(available_actions=["start_now", "defer"]))
            final = finalize_capture(start["episode_id"], tmp, script=_outcome_script())
            export = json.loads(Path(final["bridge_export_path"]).read_text(encoding="utf-8"))
            self.assertEqual(export["available_actions"], ["start_now", "defer"])
            self.assertIn("protected_outcome", export)
            for outcome_name in ["started_within_10_minutes", "start_latency_seconds"]:
                self.assertNotIn(outcome_name, export["pre_decision_features"])

    def test_source_hash_is_canonical_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            start = start_capture(tmp, script=_start_script())
            final = finalize_capture(start["episode_id"], tmp, script=_outcome_script())
            export = json.loads(Path(final["bridge_export_path"]).read_text(encoding="utf-8"))
            self.assertEqual(export["source_hash"], source_hash_for_snapshot(export))

    def test_atomic_recovery_removes_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp) / "captures" / ".partial.json.abc.tmp"
            tmp_path.parent.mkdir(parents=True)
            tmp_path.write_text("partial", encoding="utf-8")
            self.assertEqual(recover_atomic_writes(tmp), 1)
            self.assertFalse(tmp_path.exists())

    def test_collector_uses_no_network(self) -> None:
        original_socket = socket.socket

        def fail_socket(*args: object, **kwargs: object) -> object:
            raise AssertionError("network call attempted")

        socket.socket = fail_socket  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                start = start_capture(tmp, script=_start_script())
                finalize_capture(start["episode_id"], tmp, script=_outcome_script())
                status_capture(tmp)
        finally:
            socket.socket = original_socket  # type: ignore[assignment]

    def test_collector_does_not_invoke_model_prediction_code(self) -> None:
        import behavior_lab.models as models

        original_fit = models.ModelFoundry.fit_zoo
        original_predict = models.BaseRateModel.predict_proba

        def fail_fit(*args: object, **kwargs: object) -> object:
            raise AssertionError("model fitting attempted")

        def fail_predict(*args: object, **kwargs: object) -> object:
            raise AssertionError("prediction attempted")

        models.ModelFoundry.fit_zoo = fail_fit  # type: ignore[method-assign]
        models.BaseRateModel.predict_proba = fail_predict  # type: ignore[method-assign]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                start = start_capture(tmp, script=_start_script())
                finalize_capture(start["episode_id"], tmp, script=_outcome_script())
                status_capture(tmp)
        finally:
            models.ModelFoundry.fit_zoo = original_fit  # type: ignore[method-assign]
            models.BaseRateModel.predict_proba = original_predict  # type: ignore[method-assign]

    def test_cli_scripted_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            start_path = root / "start.json"
            outcome_path = root / "outcome.json"
            start_path.write_text(json.dumps(_start_script()), encoding="utf-8")
            outcome_path.write_text(json.dumps(_outcome_script()), encoding="utf-8")
            env = dict(os.environ)
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
            start_run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "behavior_lab",
                    "campaign-001-capture",
                    "start",
                    "--data-dir",
                    str(root / "data"),
                    "--script",
                    str(start_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            start_result = json.loads(start_run.stdout)
            final_run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "behavior_lab",
                    "campaign-001-capture",
                    "finalize",
                    "--data-dir",
                    str(root / "data"),
                    "--episode-id",
                    start_result["episode_id"],
                    "--script",
                    str(outcome_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            final_result = json.loads(final_run.stdout)
            self.assertEqual(final_result["ledger_record_id"], start_result["episode_id"])
            self.assertTrue(final_result["ledger_valid"])

    def test_outcome_source_enum_and_latency_consistency_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            start = start_capture(tmp, script=_start_script())
            bad_source = _outcome_script(outcome_sources={"started_within_10_minutes": "guessed"})
            with self.assertRaises(CollectorError):
                finalize_capture(start["episode_id"], tmp, script=bad_source)

        with tempfile.TemporaryDirectory() as tmp:
            start = start_capture(tmp, script=_start_script())
            with self.assertRaises(CollectorError):
                finalize_capture(
                    start["episode_id"],
                    tmp,
                    script=_outcome_script(started_within_10_minutes=True, start_latency_seconds=900),
                )

    def test_unavailable_followup_does_not_enter_bridge_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            start = start_capture(tmp, script=_start_script())
            final = finalize_capture(
                start["episode_id"],
                tmp,
                script=_outcome_script(
                    completed_that_day=None,
                    outcome_sources={
                        "started_within_10_minutes": "timer_assisted",
                        "start_latency_seconds": "timer_assisted",
                        "worked_for_20_minutes": "manual_observation",
                        "completed_that_day": "unavailable",
                    },
                ),
            )
            artifact = _read_json(final["artifact_path"])
            status = status_capture(tmp)
            ledger = ImmutableLedger(Path(tmp) / "ledger.jsonl")
            self.assertEqual(final["episode_status"], "missed_followup")
            self.assertFalse(final["imported"])
            self.assertIsNone(final["ledger_record_id"])
            self.assertFalse((Path(tmp) / "bridge_exports" / f"{start['episode_id']}.jsonl").exists())
            self.assertEqual(len(ledger.payloads("decision_episode")), 0)
            self.assertEqual(len(ledger.payloads(AUDIT_RECORD_TYPE)), 1)
            self.assertEqual(artifact["protected_outcome"]["completed_that_day"], None)
            self.assertEqual(status["episode_status_counts"]["missed_followup"], 1)

    def test_pilot_completed_episode_is_retained_but_not_model_fit_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            start = start_capture(tmp, script=_start_script(), collection_phase="pilot")
            finalize_capture(start["episode_id"], tmp, script=_outcome_script())
            ledger = ImmutableLedger(Path(tmp) / "ledger.jsonl")
            self.assertEqual(len(ledger.payloads("decision_episode")), 1)
            status = status_capture(tmp)
            self.assertEqual(status["completed_pilot_episode_count"], 1)
            self.assertEqual(status["completed_natural_episode_count"], 0)
            gym = WorldGym(tmp, campaign_id="campaign_001_task_initiation")
            self.assertEqual(gym.decision_episode_rows(), [])


if __name__ == "__main__":
    unittest.main()
