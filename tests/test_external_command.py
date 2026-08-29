import json
import hashlib
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from adapters.external_command import worker
from adapters.external_command.worker import run_trial
from regression_lab.behavior_diff import snapshot_trial_behavior


class ExternalCommandAdapterTests(unittest.TestCase):
    BLACKBOX_CAPABILITIES = {
        "schema_version": 2, "trace": True, "hierarchical_trace": False,
        "model_usage": False, "tool_trace": False, "tool_semantics": False,
        "test_trace": False, "context_trace": False, "workflow_trace": False, "mcp_trace": False,
    }

    def _worktree(self, root: Path) -> Path:
        worktree = root / "worktree"
        worktree.mkdir()
        (worktree / "calculator.py").write_text("def calculate(value):\n    return int(value) + 1\n", encoding="utf-8")
        (worktree / "test_calculator.py").write_text(
            "import unittest\nfrom calculator import calculate\nclass TestCalculator(unittest.TestCase):\n    def test_empty(self): self.assertEqual(calculate(''), 0)\n",
            encoding="utf-8",
        )
        for args in (("init",), ("config", "user.email", "test@example.invalid"), ("config", "user.name", "Test"), ("add", "."), ("commit", "-m", "baseline")):
            subprocess.run(["git", *args], cwd=worktree, check=True, capture_output=True, text=True)
        return worktree

    def _agent(self, root: Path) -> Path:
        agent = root / "agent.py"
        agent.write_text(
            "from pathlib import Path\n"
            "from regression_lab.sdk import AgentObserver\n"
            "import os\n"
            "o=AgentObserver.from_environment()\n"
            "with o.run():\n"
            "  with o.model_call(model='example-model') as call: call.record_usage({'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10})\n"
            "  with o.tool_call('edit_file', target_path='calculator.py', argument_keys=['path'], argument_fingerprint='sha256:example') as tool:\n"
            "    Path(os.environ['REGRESSION_WORKTREE'], 'calculator.py').write_text(\"def calculate(value):\\n    return 0 if value == '' else int(value) + 1\\n\", encoding='utf-8')\n"
            "    tool.preview('edited calculator.py')\n"
            "AgentObserver.write_agent_output('empty input fixed', 'model_completed')\n",
            encoding="utf-8",
        )
        return agent

    def test_external_environment_uses_an_explicit_allowlist(self):
        with mock.patch.dict(
            worker.os.environ,
            {
                "PATH": "/usr/bin",
                "OPENAI_API_KEY": "agent-key",
                "UNRELATED_PLATFORM_SECRET": "must-not-leak",
                "PYTHONPATH": "/private/platform/imports",
            },
            clear=True,
        ):
            environment = worker._external_environment()

        self.assertEqual(environment["PATH"], "/usr/bin")
        self.assertEqual(environment["OPENAI_API_KEY"], "agent-key")
        self.assertEqual(environment["PYTHONPATH"], f"{worker.ROOT / 'src'}{os.pathsep}/private/platform/imports")
        self.assertNotIn("UNRELATED_PLATFORM_SECRET", environment)

    def test_external_agent_runs_with_platform_owned_evidence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worktree, agent = self._worktree(root), self._agent(root)
            expected_hash = "sha256:" + hashlib.sha256(agent.read_bytes()).hexdigest()
            result = run_trial({
                "trial_id": "external_trial_001", "case_id": "calculator_empty", "agent_version": "example-v1",
                "adapter": {"default_version": "external-agent-v1"}, "external_command": [sys.executable, str(agent)],
                "worktree": str(worktree), "trace_output": str(root / "trace.jsonl"), "result_output": str(root / "result.json"),
                "test_command": f"{sys.executable} -m unittest test_calculator.py", "sandbox": None,
                "allowed_paths": ["calculator.py", "__pycache__/**"], "forbidden_paths": [], "allowed_tools": ["edit_file"], "denied_tools": [],
                "budget": {"max_tool_calls": 2, "max_duration_ms": 10_000},
                "expected_agent_source_hash": expected_hash,
            })
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["trace_validation"]["valid"])
        self.assertTrue(result["evaluation"]["passed"])
        self.assertEqual(result["model_usage"]["total_tokens"], 10)
        self.assertEqual(result["behavior"]["tool_success_rate"], 1.0)
        self.assertTrue(result["behavior"]["availability"]["repeated_tool_calls"])
        self.assertEqual(result["agent_response"], "empty input fixed")
        self.assertIn("calculator.py", result["changed_files"])
        self.assertEqual(result["agent_source_hash"], expected_hash)
        self.assertTrue(result["agent_source_hash_matches_protocol"])

    def test_external_agent_records_source_hash_mismatch_without_trusting_agent_output(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worktree, agent = self._worktree(root), self._agent(root)
            result = run_trial({
                "trial_id": "external_trial_source_mismatch", "case_id": "calculator_empty", "agent_version": "example-v1",
                "adapter": {}, "external_command": [sys.executable, str(agent)], "worktree": str(worktree),
                "trace_output": str(root / "trace.jsonl"), "result_output": str(root / "result.json"),
                "test_command": f"{sys.executable} -m unittest test_calculator.py", "sandbox": None,
                "allowed_paths": ["calculator.py", "__pycache__/**"], "forbidden_paths": [], "allowed_tools": ["edit_file"], "denied_tools": [],
                "budget": {"max_tool_calls": 2, "max_duration_ms": 10_000}, "expected_agent_source_hash": "sha256:frozen",
            })
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["agent_source_hash_matches_protocol"])
        self.assertNotEqual(result["agent_source_hash"], result["expected_agent_source_hash"])

    def test_source_hash_finds_entrypoint_before_agent_templates(self):
        with TemporaryDirectory() as directory:
            agent = Path(directory) / "agent.py"
            agent.write_text("print('agent')\n", encoding="utf-8")
            expected_hash = "sha256:" + hashlib.sha256(agent.read_bytes()).hexdigest()
            source_hash = worker._command_source_hash([
                sys.executable, str(agent), "--workspace", "{workspace}", "--task", "{task}",
            ])

        self.assertEqual(source_hash, expected_hash)

    def test_agent_output_cannot_override_platform_identity_or_scores(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = self._worktree(root)
            agent = root / "spoof.py"
            agent.write_text(
                "import json, os\nfrom regression_lab.sdk import AgentObserver\n"
                "o=AgentObserver.from_environment()\n"
                "with o.run(): pass\n"
                "json.dump({'trial_id':'spoofed','scores':[{'passed':True}],'agent_response':'ok','agent_exit_reason':'done'}, open(os.environ['REGRESSION_AGENT_OUTPUT_PATH'],'w'))\n",
                encoding="utf-8",
            )
            result = run_trial({
                "trial_id": "external_trial_002", "case_id": "calculator_empty", "agent_version": "example-v1",
                "adapter": {}, "external_command": [sys.executable, str(agent)], "worktree": str(worktree),
                "trace_output": str(root / "trace.jsonl"), "result_output": str(root / "result.json"), "test_command": "true", "sandbox": None,
                "allowed_paths": ["calculator.py"], "forbidden_paths": [], "allowed_tools": [], "denied_tools": [], "budget": {},
            })
        self.assertEqual(result["trial_id"], "external_trial_002")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["agent_response"], "ok")
        self.assertFalse(result["evaluation"]["passed"])
        self.assertNotEqual(result["scores"], [{"passed": True}])

    def test_model_error_exit_reason_is_classified_without_running_tests(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = self._worktree(root)
            agent = root / "model_error.py"
            agent.write_text(
                "from regression_lab.sdk import AgentObserver\n"
                "AgentObserver.write_agent_output('model unavailable', 'model_error', model_failure_kind='http_429')\n",
                encoding="utf-8",
            )
            result = run_trial({
                "trial_id": "external_trial_003", "case_id": "calculator_empty", "agent_version": "example-v1",
                "adapter": {}, "external_command": [sys.executable, str(agent)], "worktree": str(worktree),
                "trace_output": str(root / "trace.jsonl"), "result_output": str(root / "result.json"), "test_command": "false", "sandbox": None,
                "allowed_paths": ["calculator.py"], "forbidden_paths": [], "allowed_tools": [], "denied_tools": [], "budget": {},
            })
        self.assertEqual(result["status"], "model_failed")
        self.assertIsNone(result["test_exit_code"])
        self.assertEqual(result["model_failure"], {"kind": "http_429"})

    def test_timed_out_agent_is_recorded_without_reusing_its_artifact_paths(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = self._worktree(root)
            agent = root / "slow_agent.py"
            agent.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
            result = run_trial({
                "trial_id": "external_trial_004", "case_id": "calculator_empty", "agent_version": "example-v1",
                "adapter": {}, "external_command": [sys.executable, str(agent)], "worktree": str(worktree),
                "trace_output": str(root / "attempt_001" / "trace.jsonl"), "result_output": str(root / "attempt_001" / "result.json"),
                "test_command": "true", "sandbox": {"timeout_seconds": 1},
                "allowed_paths": ["calculator.py"], "forbidden_paths": [], "allowed_tools": [], "denied_tools": [], "budget": {},
            })
        self.assertEqual(result["status"], "timed_out")

    def test_blackbox_success_uses_platform_lifecycle_trace_without_agent_output(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = self._worktree(root)
            agent = root / "blackbox_agent.py"
            agent.write_text(
                "import os\nfrom pathlib import Path\n"
                "Path(os.environ['REGRESSION_WORKTREE'], 'calculator.py').write_text(\"def calculate(value):\\n    return 0 if value == '' else int(value) + 1\\n\", encoding='utf-8')\n",
                encoding="utf-8",
            )
            trace_path = root / "trace.jsonl"
            result = run_trial({
                "trial_id": "blackbox_trial_001", "case_id": "calculator_empty", "agent_version": "blackbox-v1",
                "adapter": {}, "external_command": [sys.executable, str(agent)], "worktree": str(worktree),
                "trace_output": str(trace_path), "result_output": str(root / "result.json"),
                "test_command": f"{sys.executable} -m unittest test_calculator.py", "sandbox": None,
                "allowed_paths": ["calculator.py", "__pycache__/**"], "forbidden_paths": [], "allowed_tools": ["edit_file"], "denied_tools": [],
                "budget": {"max_tool_calls": 2, "max_duration_ms": 10_000},
                "observation_mode": "blackbox", "adapter_capabilities": self.BLACKBOX_CAPABILITIES,
            })
            events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["evaluation"]["passed"])
        self.assertEqual(result["agent_response"], "")
        self.assertEqual(result["agent_output"], {
            "availability": "unavailable", "source": "not_provided",
            "reason": "blackbox mode does not require an Agent output file",
        })
        self.assertEqual(result["process_lifecycle"]["status"], "process_completed")
        self.assertTrue(result["trace_validation"]["valid"])
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["name"], "agent.run")
        self.assertEqual(events[0]["span_type"], "agent")
        self.assertEqual(events[0]["attributes"].get("observation_mode"), "blackbox")
        self.assertEqual(events[0]["attributes"].get("trace_origin"), "platform")
        self.assertEqual(events[0]["attributes"].get("trace_scope"), "process_lifecycle")
        self.assertEqual(events[1]["status"], "ok")
        self.assertEqual(result["behavior"]["capability_source"], "artifact_snapshot")
        self.assertEqual(result["behavior"]["evidence_availability"]["tool_trace"], "unsupported")
        self.assertIsNone(result["behavior"]["tool_calls"])
        snapshot = snapshot_trial_behavior(result)
        self.assertEqual(snapshot["evidence_availability"]["model_calls"], "unsupported")
        self.assertIsNone(snapshot["model_calls"])

    def test_langgraph_mode_accepts_callback_trace_without_agent_output(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = self._worktree(root)
            agent = root / "langgraph_agent.py"
            agent.write_text(
                "import os\nfrom pathlib import Path\n"
                "from regression_lab_observer.langgraph import LangGraphObserver\n"
                "with LangGraphObserver.from_environment() as observation:\n"
                "  callback=observation.callback\n"
                "  callback.on_chain_start({}, {}, run_id='node', metadata={'langgraph_node':'Coder'})\n"
                "  callback.on_chat_model_start({'kwargs':{'model_name':'fixture-model'}}, [], run_id='model', parent_run_id='node')\n"
                "  callback.on_llm_end({'llm_output':{'token_usage':{'prompt_tokens':2,'completion_tokens':1}}}, run_id='model')\n"
                "  callback.on_tool_start({'name':'edit_file'}, '', run_id='tool', parent_run_id='node')\n"
                "  Path(os.environ['REGRESSION_WORKTREE'], 'calculator.py').write_text(\"def calculate(value):\\n    return 0 if value == '' else int(value) + 1\\n\", encoding='utf-8')\n"
                "  callback.on_tool_end('', run_id='tool')\n"
                "  callback.on_chain_end({}, run_id='node')\n",
                encoding="utf-8",
            )
            result = run_trial({
                "trial_id": "langgraph_trial_001", "case_id": "calculator_empty", "agent_version": "langgraph-v1",
                "adapter": {}, "external_command": [sys.executable, str(agent)], "worktree": str(worktree),
                "trace_output": str(root / "trace.jsonl"), "result_output": str(root / "result.json"),
                "test_command": f"{sys.executable} -m unittest test_calculator.py", "sandbox": None,
                "allowed_paths": ["calculator.py", "__pycache__/**"], "forbidden_paths": [], "allowed_tools": ["edit_file"], "denied_tools": [], "budget": {},
                "observation_mode": "langgraph", "adapter_capabilities": {"schema_version": 2, "trace": True, "hierarchical_trace": True, "model_usage": True, "tool_trace": True, "tool_semantics": False, "test_trace": False, "context_trace": False, "workflow_trace": True, "mcp_trace": False},
            })

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["trace_validation"]["valid"])
        self.assertEqual(result["agent_output"]["availability"], "unavailable")
        self.assertEqual(result["model_usage"]["total_tokens"], 3)
        self.assertEqual(result["evidence_provenance"]["trace"], "framework_observed")
        self.assertTrue(result["framework_observation"]["complete"])

    def test_langgraph_mode_fails_closed_when_callback_completion_marker_is_missing(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = self._worktree(root)
            agent = root / "unobserved_langgraph_agent.py"
            agent.write_text(
                "import os\nfrom pathlib import Path\n"
                "Path(os.environ['REGRESSION_WORKTREE'], 'calculator.py').write_text(\"def calculate(value):\\n    return 0 if value == '' else int(value) + 1\\n\", encoding='utf-8')\n",
                encoding="utf-8",
            )
            result = run_trial({
                "trial_id": "langgraph_trial_missing", "case_id": "calculator_empty", "agent_version": "langgraph-v1",
                "adapter": {}, "external_command": [sys.executable, str(agent)], "worktree": str(worktree),
                "trace_output": str(root / "trace.jsonl"), "result_output": str(root / "result.json"),
                "test_command": f"{sys.executable} -m unittest test_calculator.py", "sandbox": None,
                "allowed_paths": ["calculator.py", "__pycache__/**"], "forbidden_paths": [], "allowed_tools": [], "denied_tools": [], "budget": {},
                "observation_mode": "langgraph", "adapter_capabilities": {"schema_version": 2, "trace": True, "hierarchical_trace": True, "model_usage": True, "tool_trace": True, "tool_semantics": False, "test_trace": False, "context_trace": False, "workflow_trace": True, "mcp_trace": False},
            })

        self.assertEqual(result["status"], "trace_incomplete")
        self.assertFalse(result["framework_observation"]["complete"])
        self.assertIn("status_missing", result["framework_observation"]["errors"])

    def test_blackbox_nonzero_exit_closes_platform_trace(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = self._worktree(root)
            agent = root / "fail.py"
            agent.write_text("raise SystemExit(7)\n", encoding="utf-8")
            result = run_trial({
                "trial_id": "blackbox_trial_002", "case_id": "calculator_empty", "agent_version": "blackbox-v1",
                "adapter": {}, "external_command": [sys.executable, str(agent)], "worktree": str(worktree),
                "trace_output": str(root / "trace.jsonl"), "result_output": str(root / "result.json"), "test_command": "true", "sandbox": None,
                "allowed_paths": ["calculator.py"], "forbidden_paths": [], "allowed_tools": [], "denied_tools": [], "budget": {},
                "observation_mode": "blackbox", "adapter_capabilities": self.BLACKBOX_CAPABILITIES,
            })

        self.assertEqual(result["status"], "agent_failed")
        self.assertEqual(result["agent_exit_reason"], "process_error")
        self.assertEqual(result["process_lifecycle"]["return_code"], 7)
        self.assertTrue(result["trace_validation"]["valid"])

    def test_blackbox_timeout_uses_existing_process_group_cleanup_and_closes_trace(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = self._worktree(root)
            agent = root / "slow.py"
            agent.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
            with mock.patch("adapters.external_command.worker.terminate_process_group", wraps=worker.terminate_process_group) as cleanup:
                result = run_trial({
                    "trial_id": "blackbox_trial_003", "case_id": "calculator_empty", "agent_version": "blackbox-v1",
                    "adapter": {}, "external_command": [sys.executable, str(agent)], "worktree": str(worktree),
                    "trace_output": str(root / "trace.jsonl"), "result_output": str(root / "result.json"), "test_command": "true", "sandbox": None,
                    "trial_timeout_seconds": 1,
                    "allowed_paths": ["calculator.py"], "forbidden_paths": [], "allowed_tools": [], "denied_tools": [], "budget": {},
                    "observation_mode": "blackbox", "adapter_capabilities": self.BLACKBOX_CAPABILITIES,
                })

        self.assertEqual(result["status"], "timed_out")
        self.assertEqual(result["process_lifecycle"]["status"], "deadline_exceeded")
        self.assertTrue(result["trace_validation"]["valid"])
        self.assertTrue(cleanup.called)

    def test_blackbox_resolves_workspace_and_task_in_argv_without_shell(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = self._worktree(root)
            agent = root / "arguments.py"
            agent.write_text(
                "import sys\nfrom pathlib import Path\n"
                "workspace, task = sys.argv[1:]\n"
                "assert workspace == str(Path.cwd())\nassert task == 'repair calculator; do not shell expand'\n"
                "Path('calculator.py').write_text(\"def calculate(value):\\n    return 0 if value == '' else int(value) + 1\\n\", encoding='utf-8')\n",
                encoding="utf-8",
            )
            result = run_trial({
                "trial_id": "blackbox_trial_004", "case_id": "calculator_empty", "agent_version": "blackbox-v1",
                "adapter": {}, "external_command": [sys.executable, str(agent), "{workspace}", "{task}"], "worktree": str(worktree),
                "trace_output": str(root / "trace.jsonl"), "result_output": str(root / "result.json"),
                "prompt": "repair calculator; do not shell expand", "test_command": f"{sys.executable} -m unittest test_calculator.py", "sandbox": None,
                "allowed_paths": ["calculator.py", "__pycache__/**"], "forbidden_paths": [], "allowed_tools": [], "denied_tools": [], "budget": {},
                "observation_mode": "blackbox", "adapter_capabilities": self.BLACKBOX_CAPABILITIES,
            })

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["evaluation"]["passed"])

    def test_sdk_mode_still_requires_agent_output_contract(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = self._worktree(root)
            agent = root / "sdk_missing_output.py"
            agent.write_text(
                "from regression_lab.sdk import AgentObserver\n"
                "with AgentObserver.from_environment().run(): pass\n",
                encoding="utf-8",
            )
            result = run_trial({
                "trial_id": "sdk_trial_missing_output", "case_id": "calculator_empty", "agent_version": "sdk-v1",
                "adapter": {}, "external_command": [sys.executable, str(agent)], "worktree": str(worktree),
                "trace_output": str(root / "trace.jsonl"), "result_output": str(root / "result.json"), "test_command": "true", "sandbox": None,
                "allowed_paths": ["calculator.py"], "forbidden_paths": [], "allowed_tools": [], "denied_tools": [], "budget": {},
                "adapter_capabilities": {"schema_version": 2, "trace": True, "hierarchical_trace": True, "model_usage": False, "tool_trace": False, "tool_semantics": False, "test_trace": False, "context_trace": False, "workflow_trace": False, "mcp_trace": False},
            })

        self.assertEqual(result["status"], "agent_failed")
        self.assertEqual(result["agent_exit_reason"], "contract_error")
        self.assertTrue(result["trace_validation"]["valid"])


if __name__ == "__main__":
    unittest.main()
