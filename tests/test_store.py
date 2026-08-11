import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from regression_lab.store import RunStore
from unittest.mock import patch


class RunStoreTests(unittest.TestCase):
    def test_record_get_and_filter_trial(self):
        with TemporaryDirectory() as directory:
            sqlite_path = Path(directory) / "runs.db"
            store = RunStore(sqlite_path)
            completed = {"trial_id": "trial_1", "trace_id": "trace_1", "status": "completed", "score": 1.0}
            failed = {"trial_id": "trial_2", "trace_id": "trace_2", "status": "agent_failed"}
            scores = [{"evaluator": "test", "passed": True, "actual": {"exit_code": 0}}]
            store.record_run(completed, scores)
            store.record_run(failed, [])

            self.assertEqual(store.get_trial("trial_1"), completed)
            self.assertEqual(store.get_trial("missing"), None)
            self.assertEqual(store.list_trials("completed"), [completed])
            self.assertEqual(len(store.list_trials()), 2)
            self.assertEqual(store.get_scores("trial_1"), scores)
            records = [__import__("json").loads(line) for line in (Path(directory) / "runs.jsonl").read_text().splitlines()]
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["kind"], "trial_recorded")
            self.assertIn("audit_id", records[0])

    def test_record_trial_requires_identity_fields(self):
        with TemporaryDirectory() as directory:
            store = RunStore(Path(directory) / "runs.db")
            with self.assertRaises(ValueError):
                store.record_trial({"trial_id": "trial_1", "status": "completed"})

    def test_sqlite_commit_survives_jsonl_failure_and_is_recovered(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "runs.db"
            store = RunStore(path)
            result = {"trial_id": "trial_1", "trace_id": "trace_1", "status": "completed"}
            with patch.object(store, "_append_audit", side_effect=OSError("disk unavailable")):
                with self.assertRaises(OSError):
                    store.record_run(result, [])

            self.assertEqual(store.get_trial("trial_1"), result)
            self.assertEqual(store.pending_audit_count(), 1)
            recovered = RunStore(path)
            self.assertEqual(recovered.pending_audit_count(), 0)
            self.assertEqual(len(recovered.get_scores("trial_1")), 0)

    def test_record_run_replaces_stale_score_set(self):
        with TemporaryDirectory() as directory:
            store = RunStore(Path(directory) / "runs.db")
            result = {"trial_id": "trial_1", "trace_id": "trace_1", "status": "completed"}
            store.record_run(result, [{"evaluator": "old", "passed": True}])
            store.record_run(result, [{"evaluator": "new", "passed": False}])

            self.assertEqual([score["evaluator"] for score in store.get_scores("trial_1")], ["new"])


if __name__ == "__main__":
    unittest.main()
