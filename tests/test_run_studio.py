import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from scripts.serve_studio import REGRESSION, STUDIO_HOST, _prepared_request, _prepared_request_with_snapshots, command_for, main, preflight


class RunStudioTests(unittest.TestCase):
    def _request(self, baseline: Path, candidate: Path, **overrides):
        request = {
            "baseline": str(baseline), "candidate": str(candidate), "benchmarks": ["smoke-case-design.yaml"],
            "trials": 3, "execution_mode": "trusted_host", "trusted_host_confirmed": True,
        }
        request.update(overrides)
        return request

    def test_preflight_accepts_valid_agent_specs_and_reports_trial_count(self):
        with TemporaryDirectory() as directory:
            baseline, candidate = Path(directory) / "baseline.json", Path(directory) / "candidate.json"
            for path, version in ((baseline, "v1"), (candidate, "v2")):
                path.write_text(json.dumps({
                    "schema_version": 1, "project_id": "studio-fixture", "agent": {"id": "studio-agent", "version": version},
                    "runtime": {"command": [sys.executable]}, "observation": {"mode": "blackbox"},
                }), encoding="utf-8")
            result = preflight(self._request(baseline, candidate))

        self.assertTrue(result["valid"])
        self.assertEqual(result["configuration"]["trial_count"], 6)
        self.assertIn("当前主机", result["warnings"][0])

    def test_preflight_requires_explicit_trusted_host_confirmation(self):
        result = preflight({"execution_mode": "trusted_host", "trusted_host_confirmed": False})

        self.assertFalse(result["valid"])
        self.assertIn("请确认仅在可信主机上运行 Agent", result["errors"])

    def test_quick_setup_generates_valid_internal_agent_specs(self):
        request = {
            "launch_mode": "quick", "project_id": "quick-project", "agent_id": "quick-agent",
            "baseline_version": "v1", "candidate_version": "v2", "baseline_python_executable": sys.executable, "candidate_python_executable": sys.executable,
            "baseline_entrypoint": str(REGRESSION / "examples" / "external_blackbox_agent.py"),
            "candidate_entrypoint": str(REGRESSION / "examples" / "external_blackbox_agent.py"),
            "workspace_flag": "--workspace", "task_flag": "--task", "observation_mode": "blackbox",
            "benchmarks": ["smoke-case-design.yaml"], "trials": 3,
            "execution_mode": "trusted_host", "trusted_host_confirmed": True,
        }

        result = preflight(request)
        with TemporaryDirectory() as directory, mock.patch("scripts.serve_studio.REGRESSION", Path(directory)):
            prepared = _prepared_request(request)
            self.assertTrue(Path(prepared["baseline"]).is_file())
            self.assertNotEqual(prepared["baseline"], prepared["candidate"])

        self.assertTrue(result["valid"])
        self.assertEqual(result["configuration"]["agent_id"], "quick-agent")

    def test_quick_setup_accepts_installed_python_module_targets(self):
        request = {
            "launch_mode": "quick", "project_id": "module-project", "agent_id": "module-agent",
            "baseline_version": "v1", "candidate_version": "v2", "baseline_python_executable": sys.executable, "candidate_python_executable": sys.executable,
            "launch_target_kind": "module", "baseline_entrypoint": "example_agent", "candidate_entrypoint": "example_agent",
            "observation_mode": "blackbox", "benchmarks": ["smoke-case-design.yaml"], "trials": 1,
            "execution_mode": "trusted_host", "trusted_host_confirmed": True,
        }

        result = preflight(request)

        self.assertTrue(result["valid"])

    def test_git_quick_setup_freezes_a_dirty_candidate_without_touching_repository(self):
        with TemporaryDirectory() as directory:
            repository = Path(directory)
            for arguments in (("init",), ("config", "user.email", "test@example.invalid"), ("config", "user.name", "Studio Test")):
                subprocess.run(["git", *arguments], cwd=repository, check=True, capture_output=True)
            (repository / "agent.py").write_text("VERSION = 'baseline'\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repository, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "baseline"], cwd=repository, check=True, capture_output=True)
            (repository / "agent.py").write_text("VERSION = 'candidate'\n", encoding="utf-8")
            original_status = subprocess.run(["git", "status", "--porcelain"], cwd=repository, text=True, capture_output=True, check=True).stdout
            request = {
                "launch_mode": "quick", "source_mode": "git_repository", "repository_path": str(repository),
                "baseline_ref": "HEAD", "candidate_source": "working_tree", "project_id": "git-project", "agent_id": "git-agent",
                "baseline_version": "v1", "candidate_version": "working-tree", "baseline_python_executable": sys.executable,
                "candidate_python_executable": sys.executable, "launch_target_kind": "script", "baseline_entrypoint": "agent.py", "candidate_entrypoint": "agent.py",
                "observation_mode": "blackbox", "benchmarks": ["smoke-case-design.yaml"], "trials": 1,
                "execution_mode": "trusted_host", "trusted_host_confirmed": True,
            }
            preflight_result = preflight(request)
            prepared, snapshots = _prepared_request_with_snapshots(request)
            try:
                self.assertTrue(preflight_result["valid"])
                self.assertTrue(preflight_result["configuration"]["git_sources"]["candidate_dirty"])
                self.assertIn("{agent_source}/agent.py", Path(prepared["baseline"]).read_text(encoding="utf-8"))
                self.assertIsNotNone(snapshots)
                self.assertEqual((snapshots.baseline_root / "agent.py").read_text(encoding="utf-8"), "VERSION = 'baseline'\n")
                self.assertEqual((snapshots.candidate_root / "agent.py").read_text(encoding="utf-8"), "VERSION = 'candidate'\n")
            finally:
                if snapshots:
                    snapshots.cleanup()
            current_status = subprocess.run(["git", "status", "--porcelain"], cwd=repository, text=True, capture_output=True, check=True).stdout
            self.assertEqual(current_status, original_status)

    def test_git_quick_setup_runs_an_experiment_from_frozen_sources(self):
        with TemporaryDirectory() as directory, TemporaryDirectory() as runtime_directory:
            repository = Path(directory)
            for arguments in (("init",), ("config", "user.email", "test@example.invalid"), ("config", "user.name", "Studio Test")):
                subprocess.run(["git", *arguments], cwd=repository, check=True, capture_output=True)
            agent = repository / "agent.py"
            agent.write_text(
                "import argparse\nfrom pathlib import Path\n"
                "parser = argparse.ArgumentParser(); parser.add_argument('--workspace'); parser.add_argument('--task'); args = parser.parse_args()\n"
                "Path(args.workspace, 'src', 'calculator.py').write_text(\"def calculate(value):\\n    return 0 if value == '' else int(value) + 1\\n\", encoding='utf-8')\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=repository, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "baseline"], cwd=repository, check=True, capture_output=True)
            agent.write_text("# candidate snapshot\n" + agent.read_text(encoding="utf-8"), encoding="utf-8")
            original_status = subprocess.run(["git", "status", "--porcelain"], cwd=repository, text=True, capture_output=True, check=True).stdout
            request = {
                "launch_mode": "quick", "source_mode": "git_repository", "repository_path": str(repository),
                "baseline_ref": "HEAD", "candidate_source": "working_tree", "project_id": "git-e2e", "agent_id": "git-agent",
                "baseline_version": "v1", "candidate_version": "working-tree", "baseline_python_executable": sys.executable,
                "candidate_python_executable": sys.executable, "launch_target_kind": "script", "baseline_entrypoint": "agent.py", "candidate_entrypoint": "agent.py",
                "observation_mode": "blackbox", "benchmarks": ["smoke-case-design.yaml"], "trials": 1,
                "execution_mode": "trusted_host", "trusted_host_confirmed": True,
            }
            prepared, snapshots = _prepared_request_with_snapshots(request)
            try:
                completed = subprocess.run(
                    command_for(prepared), cwd=REGRESSION, text=True, capture_output=True, check=False,
                    env={**os.environ, "REGRESSION_LAB_HOME": runtime_directory},
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                runtime = Path(next(line.removeprefix("Runtime: ") for line in completed.stdout.splitlines() if line.startswith("Runtime: ")))
                protocol = json.loads((runtime / "protocol.json").read_text(encoding="utf-8"))
                snapshots_by_label = {item["label"]: item["agent_spec_snapshot"] for item in protocol["agents"]}
                self.assertFalse(snapshots_by_label["baseline"]["source_dirty"])
                self.assertTrue(snapshots_by_label["candidate"]["source_dirty"])
                self.assertNotEqual(snapshots_by_label["baseline"]["agent_source_hash"], snapshots_by_label["candidate"]["agent_source_hash"])
            finally:
                if snapshots:
                    snapshots.cleanup()
            current_status = subprocess.run(["git", "status", "--porcelain"], cwd=repository, text=True, capture_output=True, check=True).stdout
            self.assertEqual(current_status, original_status)

    def test_quick_setup_accepts_langgraph_without_manual_capabilities(self):
        request = {
            "launch_mode": "quick", "project_id": "graph-project", "agent_id": "graph-agent",
            "baseline_version": "v1", "candidate_version": "v2", "baseline_python_executable": sys.executable, "candidate_python_executable": sys.executable,
            "baseline_entrypoint": str(REGRESSION / "examples" / "langgraph_coding_agent.py"),
            "candidate_entrypoint": str(REGRESSION / "examples" / "langgraph_coding_agent.py"),
            "observation_mode": "langgraph", "benchmarks": ["smoke-case-design.yaml"], "trials": 1,
            "execution_mode": "trusted_host", "trusted_host_confirmed": True,
        }

        result = preflight(request)

        self.assertTrue(result["valid"])
        self.assertTrue(any("Callback" in warning for warning in result["warnings"]))

    def test_preflight_rejects_unknown_benchmark_and_out_of_range_trials(self):
        result = preflight({"benchmarks": ["outside.yaml"], "trials": 99})

        self.assertFalse(result["valid"])
        self.assertIn("Benchmark 选择无效，请刷新页面后重试", result["errors"])
        self.assertIn("重复次数必须是 1 到 10 的整数", result["errors"])

    def test_command_uses_fixed_cli_and_selected_manifest_only(self):
        request = {"baseline": "/tmp/baseline.yaml", "candidate": "/tmp/candidate.yaml", "benchmarks": ["smoke-case-design.yaml"], "trials": 3, "execution_mode": "trusted_host"}
        command = command_for(request)

        self.assertEqual(command[:4], [sys.executable, str(REGRESSION / "scripts" / "regression_lab.py"), "experiment", "run"])
        self.assertIn("--unsafe-trusted-host", command)
        self.assertIn(str(REGRESSION / "benchmarks" / "smoke-case-design.yaml"), command)

    def test_studio_server_is_always_bound_to_loopback(self):
        with mock.patch("scripts.serve_studio.ThreadingHTTPServer") as server_class, \
             mock.patch.object(sys, "argv", ["serve_studio.py", "--port", "9123"]):
            server = server_class.return_value
            server.serve_forever.side_effect = KeyboardInterrupt
            self.assertEqual(main(), 0)

        self.assertEqual(server_class.call_args.args[0], (STUDIO_HOST, 9123))


if __name__ == "__main__":
    unittest.main()
