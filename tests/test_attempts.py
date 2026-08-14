import json
import multiprocessing
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.attempts import AttemptManager
from regression_lab.artifacts import write_json_atomically


def _hold_trial_lock(directory: str, ready: object, release: object) -> None:
    manager = AttemptManager(Path(directory) / "trial_001", job_id="trial_001", fingerprint="sha256:test")
    manager.acquire_trial_lock()
    ready.set()
    release.wait(5)  # type: ignore[attr-defined]
    manager.release_trial_lock()


class AttemptManagerTests(unittest.TestCase):
    def test_attempts_are_numbered_and_keep_independent_artifacts(self):
        with TemporaryDirectory() as directory:
            manager = AttemptManager(Path(directory) / "trial_001", job_id="trial_001", fingerprint="sha256:test")
            first = manager.create_attempt()
            first.trace.write_text("first evidence\n", encoding="utf-8")
            manager.finish_attempt(first, "invalid", error="trace validation failed")
            second = manager.create_attempt()

            self.assertEqual(first.attempt_id, "attempt_001")
            self.assertEqual(second.attempt_id, "attempt_002")
            self.assertNotEqual(first.trace, second.trace)
            self.assertEqual(first.trace.read_text(encoding="utf-8"), "first evidence\n")
            self.assertEqual(json.loads(first.manifest.read_text())['status'], "invalid")
            self.assertEqual(json.loads(second.manifest.read_text())['status'], "running")

    def test_only_terminal_owned_attempt_can_be_selected(self):
        with TemporaryDirectory() as directory:
            manager = AttemptManager(
                Path(directory) / "trial_001", job_id="trial_001", fingerprint="sha256:test",
                protocol_fingerprint="sha256:protocol", schedule_index=7,
            )
            attempt = manager.create_attempt()
            with self.assertRaisesRegex(ValueError, "running"):
                manager.select_attempt(attempt)
            manager.finish_attempt(attempt, "completed")
            manager.select_attempt(attempt)

            selected = json.loads(manager.selected_path.read_text(encoding="utf-8"))
            self.assertEqual(selected["attempt_id"], "attempt_001")
            self.assertEqual(selected["protocol_fingerprint"], "sha256:protocol")
            self.assertEqual(selected["schedule_index"], 7)
            attempt_manifest = json.loads(attempt.manifest.read_text(encoding="utf-8"))
            self.assertEqual(attempt_manifest["protocol_fingerprint"], "sha256:protocol")

    def test_finish_rejects_non_terminal_status(self):
        with TemporaryDirectory() as directory:
            manager = AttemptManager(Path(directory) / "trial_001", job_id="trial_001", fingerprint="sha256:test")
            attempt = manager.create_attempt()
            with self.assertRaisesRegex(ValueError, "unsupported"):
                manager.finish_attempt(attempt, "running")

    def test_latest_terminal_attempt_is_selected_and_prior_failure_is_retained(self):
        with TemporaryDirectory() as directory:
            manager = AttemptManager(Path(directory) / "trial_001", job_id="trial_001", fingerprint="sha256:test")
            passed = manager.create_attempt()
            manager.finish_attempt(passed, "completed")
            passed.result.write_text(json.dumps({"status": "completed", "evaluation": {"passed": True}, "trace_validation": {"valid": True}}), encoding="utf-8")
            failed = manager.create_attempt()
            manager.finish_attempt(failed, "completed", error="provider unavailable")
            failed.result.write_text(json.dumps({"status": "model_failed", "evaluation": {"passed": False}, "trace_validation": {"valid": True}}), encoding="utf-8")

            selected, result = manager.select_latest_terminal_attempt() or (None, None)
            self.assertEqual(selected, failed)
            self.assertFalse(result["evaluation"]["passed"])
            projection = json.loads(manager.selected_path.read_text(encoding="utf-8"))
            self.assertEqual(projection["selection_policy"], "latest_terminal_attempt_v1")
            self.assertEqual(projection["selection_reason"], "latest_terminal_attempt")
            self.assertEqual(projection["attempt_count"], 2)

    def test_existing_selected_projection_is_not_re_ranked(self):
        with TemporaryDirectory() as directory:
            manager = AttemptManager(Path(directory) / "trial_001", job_id="trial_001", fingerprint="sha256:test")
            first = manager.create_attempt()
            manager.finish_attempt(first, "completed")
            first.result.write_text(json.dumps({"status": "completed", "evaluation": {"passed": True}}), encoding="utf-8")
            manager.select_attempt(first, reason="operator_approved_retry_policy")
            second = manager.create_attempt()
            manager.finish_attempt(second, "completed")
            second.result.write_text(json.dumps({"status": "model_failed", "evaluation": {"passed": False}}), encoding="utf-8")

            selected, result = manager.resolve_selected_attempt() or (None, None)
            self.assertEqual(selected, first)
            self.assertTrue(result["evaluation"]["passed"])

    def test_same_trial_cannot_acquire_two_concurrent_locks(self):
        with TemporaryDirectory() as directory:
            context = multiprocessing.get_context("spawn")
            ready, release = context.Event(), context.Event()
            process = context.Process(target=_hold_trial_lock, args=(directory, ready, release))
            process.start()
            self.assertTrue(ready.wait(5))
            manager = AttemptManager(Path(directory) / "trial_001", job_id="trial_001", fingerprint="sha256:test")
            with self.assertRaisesRegex(RuntimeError, "already running"):
                manager.acquire_trial_lock()
            release.set()
            process.join(5)
            self.assertEqual(process.exitcode, 0)

    def test_orphaned_running_attempt_is_aborted_without_overwriting_evidence(self):
        with TemporaryDirectory() as directory:
            manager = AttemptManager(Path(directory) / "trial_001", job_id="trial_001", fingerprint="sha256:test")
            manager.acquire_trial_lock()
            attempt = manager.create_attempt()
            attempt.trace.write_text("partial trace\n", encoding="utf-8")
            recovered = manager.recover_orphaned_attempts()
            manager.release_trial_lock()
            self.assertEqual(recovered, ["attempt_001"])
            manifest = json.loads(attempt.manifest.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "aborted")
            self.assertEqual(attempt.trace.read_text(encoding="utf-8"), "partial trace\n")

    def test_atomic_json_publish_never_exposes_partial_document(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / "artifact.json"
            write_json_atomically(target, {"version": 1})
            write_json_atomically(target, {"version": 2, "items": [1, 2, 3]})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"version": 2, "items": [1, 2, 3]})


if __name__ == "__main__":
    unittest.main()
