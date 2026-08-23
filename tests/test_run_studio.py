import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from scripts.serve_studio import REGRESSION, STUDIO_HOST, _prepared_request, command_for, main, preflight


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
