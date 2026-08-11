import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from adapters.s20.worker import resolve_tool_policy


REGRESSION = Path(__file__).resolve().parents[1]


class ToolPolicyTests(unittest.TestCase):
    def test_denied_tool_is_removed_from_effective_policy(self):
        effective, denied = resolve_tool_policy({
            "allowed_tools": ["read_file", "edit_file"],
            "denied_tools": ["edit_file"],
        })

        self.assertEqual(effective, frozenset({"read_file"}))
        self.assertIn("edit_file", denied)
        self.assertIn("spawn_teammate", denied)

    def test_unknown_or_permanently_denied_tool_fails_closed(self):
        with self.assertRaises(ValueError):
            resolve_tool_policy({"allowed_tools": ["unknown_tool"]})
        with self.assertRaises(ValueError):
            resolve_tool_policy({"allowed_tools": ["connect_mcp"]})

    def test_manifest_policy_blocks_replayed_edit(self):
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            manifest = {
                "schema_version": 1,
                "id": "policy_read_only",
                "version": 1,
                "title": "read only policy",
                "fixture": {
                    "path": "fixtures/smoke_calculator",
                    "test_command": "python -m unittest discover -s tests -v",
                },
                "task": {
                    "prompt": "repair calculator",
                    "allowed_paths": ["src/**"],
                    "forbidden_paths": ["tests/**"],
                },
                "execution": {
                    "timeout_seconds": 30,
                    "max_tokens": 1000,
                    "max_tool_calls": 5,
                    "trials": 1,
                    "network": "none",
                },
                "tool_policy": {"allow": ["read_file"], "deny": ["edit_file"]},
                "evaluators": {"required": ["test"]},
                "acceptance": {"must": ["test_exit_code == 0"]},
            }
            manifest_path = runtime / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            output = runtime / "output"
            completed = subprocess.run(
                [
                    sys.executable, "scripts/run_benchmark.py",
                    "--manifest", str(manifest_path),
                    "--output-dir", str(output),
                    "--unsafe-trusted-host",
                ],
                cwd=REGRESSION,
                capture_output=True,
                text=True,
                check=False,
            )
            result_path = output / "policy_read_only_trial_001" / "result.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(result["allowed_tools"], ["read_file"])
        self.assertEqual(result["changed_files"], [])
        self.assertNotEqual(result["test_exit_code"], 0)
        self.assertTrue(any(
            score["evaluator"] == "tool_integrity" and score["passed"]
            for score in result["scores"]
        ))


if __name__ == "__main__":
    unittest.main()
