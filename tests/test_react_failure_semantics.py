import json
import shutil
import subprocess
import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from adapters.react_agent.model_client import ModelClientError, ModelReply, ToolCall
from adapters.react_agent import worker
from adapters.react_agent.worker import run_trial
from regression_lab.schema import TraceValidation


REGRESSION = Path(__file__).resolve().parents[1]


class ScriptedClient:
    model = "scripted-test-model"

    def __init__(self, replies):
        self.replies = iter(replies)

    def complete(self, messages, tools, max_tokens):
        return next(self.replies)


class FailingClient:
    model = "failing-test-model"

    def complete(self, messages, tools, max_tokens):
        raise ModelClientError("model HTTP error: 503")


class ReactFailureSemanticsTests(unittest.TestCase):
    def _spec(self, directory: Path) -> dict:
        worktree = directory / "worktree"
        shutil.copytree(REGRESSION / "fixtures" / "smoke_calculator", worktree)
        for args in (("init",), ("config", "user.email", "test@example.invalid"), ("config", "user.name", "Regression Test"), ("add", "."), ("commit", "-m", "fixture")):
            subprocess.run(["git", *args], cwd=worktree, check=True, capture_output=True, text=True)
        return {
            "trial_id": "failure_semantics_trial",
            "agent_version": "react-agent-v1",
            "adapter": {"default_version": "react-agent-v1"},
            "case_id": "failure_semantics",
            "prompt": "repair only src",
            "worktree": str(worktree),
            "test_command": "python3.11 -m unittest discover -s tests -v",
            "sandbox": None,
            "allowed_paths": ["src/**"],
            "forbidden_paths": ["tests/**"],
            "allowed_tools": ["read_file", "edit_file", "write_file"],
            "denied_tools": [],
            "budget": {"max_tool_calls": 3, "max_duration_ms": 30000},
            "max_tokens": 1000,
            "trace_output": str(directory / "trace.jsonl"),
            "result_output": str(directory / "result.json"),
            "run_store": None,
        }

    def test_forbidden_path_is_denied_before_test_can_be_modified(self):
        with TemporaryDirectory() as raw:
            directory = Path(raw); spec = self._spec(directory)
            before = (Path(spec["worktree"]) / "tests" / "test_calculator.py").read_text(encoding="utf-8")
            client = ScriptedClient([
                ModelReply("", (ToolCall("call_1", "edit_file", {"path": "tests/test_calculator.py", "old_text": "self.assertEqual(calculate(\"\"), 0)", "new_text": "self.assertEqual(calculate(\"\"), 1)"}),), "tool_calls", {}),
                ModelReply("done", (), "stop", {}),
            ])
            result = run_trial(spec, client)
            after = (Path(spec["worktree"]) / "tests" / "test_calculator.py").read_text(encoding="utf-8")
            trace = [json.loads(line) for line in Path(spec["trace_output"]).read_text(encoding="utf-8").splitlines()]

        self.assertEqual(result["status"], "agent_failed")
        self.assertEqual(before, after)
        self.assertEqual(result["changed_files"], [])
        self.assertFalse(result["evaluation"]["passed"])
        tool_span_ids = {
            event.get("span_id") for event in trace
            if event.get("kind") == "span_start" and event.get("name") == "tool.call"
        }
        self.assertTrue(any(
            event.get("kind") == "span_end" and event.get("span_id") in tool_span_ids and event.get("status") == "denied"
            for event in trace
        ))

    def test_model_failure_is_persisted_as_a_distinct_non_passing_trial(self):
        with TemporaryDirectory() as raw:
            directory = Path(raw); spec = self._spec(directory)
            result = run_trial(spec, FailingClient())

        self.assertEqual(result["status"], "model_failed")
        self.assertEqual(result["agent_exit_reason"], "model_error")
        self.assertTrue(result["trace_validation"]["valid"])
        self.assertFalse(result["evaluation"]["passed"])

    def test_invalid_trace_cannot_be_marked_completed_or_reused(self):
        with TemporaryDirectory() as raw:
            directory = Path(raw); spec = self._spec(directory)
            client = ScriptedClient([
                ModelReply("", (ToolCall("call_1", "edit_file", {"path": "src/calculator.py", "old_text": "return int(value) + 1", "new_text": "if value == \"\":\n        return 0\n    return int(value) + 1"}),), "tool_calls", {}),
                ModelReply("done", (), "stop", {}),
            ])
            invalid = TraceValidation(False, "trace_test", 1, 1, ("forced invalid trace",))
            with patch.object(worker, "validate_trace", return_value=invalid):
                result = worker.run_trial(spec, client)

        self.assertEqual(result["status"], "trace_incomplete")
        self.assertEqual(result["error"], "trace validation failed")
        self.assertFalse(result["evaluation"]["passed"])
