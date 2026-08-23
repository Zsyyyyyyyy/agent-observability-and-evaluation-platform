import json
import os
import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.agent_spec import AgentSpecError, load_agent_spec


class AgentSpecTests(unittest.TestCase):
    def _write(self, root: Path, value: dict) -> Path:
        path = root / "agent.yaml"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def _base(self, command: list[str]) -> dict:
        return {
            "schema_version": 1,
            "agent": {"id": "my-agent", "version": "v1"},
            "runtime": {"command": command},
            "observation": {"mode": "sdk"},
        }

    def test_sdk_spec_normalizes_capabilities_and_command_templates(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "agent.py"
            source.write_text("", encoding="utf-8")
            value = self._base([sys.executable, str(source), "--workspace={workspace}", "--task", "{task}"])
            value["observation"]["capabilities"] = {"tool_trace": True, "tool_semantics": True, "model_usage": True}
            spec = load_agent_spec(self._write(Path(directory), value))

        self.assertEqual(spec.resolve_command(workspace="/tmp/worktree", task="repair bug"), [
            sys.executable, str(source), "--workspace=/tmp/worktree", "--task", "repair bug",
        ])
        self.assertEqual(spec.capabilities.as_dict(), {
            "schema_version": 2, "trace": True, "hierarchical_trace": True,
            "model_usage": True, "tool_trace": True, "tool_semantics": True,
            "test_trace": False, "context_trace": False, "workflow_trace": False, "mcp_trace": False,
        })

    def test_blackbox_derives_conservative_capabilities(self):
        with TemporaryDirectory() as directory:
            value = self._base([sys.executable])
            value["observation"] = {"mode": "blackbox"}
            spec = load_agent_spec(self._write(Path(directory), value))

        self.assertEqual(spec.capabilities.as_dict(), {
            "schema_version": 2, "trace": True, "hierarchical_trace": False,
            "model_usage": False, "tool_trace": False, "tool_semantics": False,
            "test_trace": False, "context_trace": False, "workflow_trace": False, "mcp_trace": False,
        })
        self.assertEqual(spec.as_external_command_config()["adapter"], "external-command")

    def test_rejects_shell_command_unknown_templates_and_internal_fields(self):
        with TemporaryDirectory() as directory:
            value = self._base([sys.executable, "{trace_path}"])
            value["trial_id"] = "forbidden"
            with self.assertRaisesRegex(AgentSpecError, "platform-owned") as error:
                load_agent_spec(self._write(Path(directory), value))

        self.assertIn("unsupported template {trace_path}", str(error.exception))

    def test_rejects_shell_command_string(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(AgentSpecError, "argv list"):
                load_agent_spec(self._write(Path(directory), self._base("python my-agent.py")))

    def test_rejects_invalid_sdk_capability_dependency(self):
        with TemporaryDirectory() as directory:
            value = self._base([sys.executable])
            value["observation"]["capabilities"] = {"tool_semantics": True}
            with self.assertRaisesRegex(AgentSpecError, "requires tool_trace"):
                load_agent_spec(self._write(Path(directory), value))

    def test_validate_is_static_and_does_not_start_command(self):
        with TemporaryDirectory() as directory:
            marker = Path(directory) / "started"
            agent = Path(directory) / "agent.py"
            agent.write_text(f"from pathlib import Path; Path({str(marker)!r}).write_text('started')", encoding="utf-8")
            spec = self._write(Path(directory), self._base([sys.executable, str(agent)]))
            loaded = load_agent_spec(spec)
            self.assertEqual(loaded.agent_id, "my-agent")
            self.assertFalse(marker.exists())

    def test_snapshot_is_canonical_and_scoped_to_the_entrypoint(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "agent.py"
            source.write_text("print('agent')\n", encoding="utf-8")
            spec = load_agent_spec(self._write(Path(directory), self._base([sys.executable, str(source)])))
            first, second = spec.snapshot(), spec.snapshot()

        self.assertEqual(first["agent_spec_hash"], second["agent_spec_hash"])
        self.assertEqual(first["entrypoint_hash"], second["entrypoint_hash"])
        self.assertEqual(first["source_scope"], "entrypoint_only")
        self.assertEqual(first["normalized_command"], [sys.executable, str(source)])

    def test_snapshot_hashes_direct_executable_before_workspace_and_task_templates(self):
        with TemporaryDirectory() as directory:
            executable = Path(directory) / "agent"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
            spec = load_agent_spec(self._write(Path(directory), self._base([
                str(executable), "--workspace", "{workspace}", "--task", "{task}",
            ])))
            snapshot = spec.snapshot()

        self.assertIsInstance(snapshot["entrypoint_hash"], str)
        self.assertEqual(snapshot["source_scope"], "entrypoint_only")

    def test_snapshot_hashes_the_agent_git_worktree_not_just_its_entrypoint(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "agent.py"
            dependency = root / "prompts.py"
            source.write_text("from prompts import SYSTEM\n", encoding="utf-8")
            dependency.write_text("SYSTEM = 'first'\n", encoding="utf-8")
            for command in (["git", "init"], ["git", "add", "."], ["git", "commit", "-m", "initial"]):
                subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)
            spec = load_agent_spec(self._write(root, self._base([sys.executable, str(source)])))
            before = spec.snapshot()
            dependency.write_text("SYSTEM = 'changed'\n", encoding="utf-8")
            after = spec.snapshot()

        self.assertEqual(before["source_scope"], "git_worktree")
        self.assertEqual(before["entrypoint_hash"], after["entrypoint_hash"])
        self.assertNotEqual(before["agent_source_hash"], after["agent_source_hash"])

    def test_snapshot_hashes_python_module_worktree_not_its_interpreter(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "demo_agent"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "__main__.py").write_text("from .prompts import SYSTEM\n", encoding="utf-8")
            prompts = package / "prompts.py"
            prompts.write_text("SYSTEM = 'first'\n", encoding="utf-8")
            for command in (["git", "init"], ["git", "add", "."], ["git", "commit", "-m", "initial"]):
                subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)
            with mock.patch.dict(os.environ, {"PYTHONPATH": str(root)}, clear=False):
                spec = load_agent_spec(self._write(root, self._base([
                    sys.executable, "-m", "demo_agent", "--workspace", "{workspace}", "--task", "{task}",
                ])))
                before = spec.snapshot()
                prompts.write_text("SYSTEM = 'changed'\n", encoding="utf-8")
                after = spec.snapshot()

        self.assertEqual(before["source_scope"], "git_worktree")
        self.assertNotEqual(before["agent_source_hash"], after["agent_source_hash"])

    def test_project_id_is_normalized_into_the_agent_snapshot(self):
        with TemporaryDirectory() as directory:
            value = self._base([sys.executable])
            value["project_id"] = "coding-agent-platform"
            spec = load_agent_spec(self._write(Path(directory), value))

        self.assertEqual(spec.project_id, "coding-agent-platform")
        self.assertEqual(spec.snapshot()["project_id"], "coding-agent-platform")

    def test_project_id_rejects_path_like_values(self):
        with TemporaryDirectory() as directory:
            value = self._base([sys.executable])
            value["project_id"] = "../other-project"
            with self.assertRaisesRegex(AgentSpecError, "project_id must match"):
                load_agent_spec(self._write(Path(directory), value))


if __name__ == "__main__":
    unittest.main()
