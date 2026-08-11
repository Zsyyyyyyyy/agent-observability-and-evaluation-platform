import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.dashboard import DashboardRepository


class DashboardRepositoryTests(unittest.TestCase):
    def test_aggregates_trials_and_blocks_path_escape(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); trial = root / "baseline" / "case"; trial.mkdir(parents=True)
            trace = trial / "trace.jsonl"; trace.write_text('{"kind":"event","name":"agent.stop"}\n', encoding="utf-8")
            result = {"trial_id": "case_trial_001", "status": "completed", "agent_version": "v1", "trace_path": str(trace), "evaluation": {"passed": True}, "model_usage": {"total_tokens": 12}, "scores": [{"evaluator": "budget", "actual": {"duration_ms": 10}}, {"evaluator": "tool_integrity", "actual": {"tool_calls": 2}}]}
            (trial / "result.json").write_text(json.dumps(result), encoding="utf-8")
            archived = root / "invalid-attempts" / "attempt"; archived.mkdir(parents=True)
            (archived / "result.json").write_text(json.dumps({**result, "status": "trace_incomplete"}), encoding="utf-8")
            repo = DashboardRepository(root)

            dashboard = repo.dashboard()
            self.assertEqual(dashboard["pass_rate"], 1.0)
            self.assertEqual(dashboard["trace_incomplete_count"], 0)
            self.assertEqual(dashboard["runtime_label"], f"Local experiment artifacts · {root.name}")
            self.assertNotIn(str(root), dashboard["runtime_label"])
            self.assertEqual(repo.trials()[0]["case_id"], "case")
            self.assertEqual(repo.trials()[0]["id"], "baseline/case")
            self.assertEqual(repo.trial("baseline/case")["trace"][0]["name"], "agent.stop")
            self.assertIsNone(repo.trial("../../etc"))
