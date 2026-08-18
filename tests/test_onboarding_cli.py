import json
import subprocess
import sys
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from scripts.regression_lab import _console_port, _print_smoke, _serve_console, _validate_experiment_specs


ROOT = Path(__file__).resolve().parents[1]


class OnboardingCliTests(unittest.TestCase):
    def test_validate_prints_user_facing_static_result(self):
        with TemporaryDirectory() as directory:
            spec = Path(directory) / "agent.yaml"
            spec.write_text(json.dumps({
                "schema_version": 1,
                "agent": {"id": "demo-agent", "version": "v1"},
                "runtime": {"command": [sys.executable]},
                "observation": {"mode": "blackbox"},
            }), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "scripts/regression_lab.py", "agent", "validate", str(spec)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Agent spec is valid", completed.stdout)
        self.assertIn("No Agent process, model call, Trial, or Artifact was created.", completed.stdout)
        self.assertNotIn("blackbox execution is planned", completed.stdout)

    def test_validate_reports_configuration_errors_without_raw_traceback(self):
        with TemporaryDirectory() as directory:
            spec = Path(directory) / "agent.yaml"
            spec.write_text("schema_version: 1\nagent: invalid\n", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "scripts/regression_lab.py", "agent", "validate", str(spec)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("Agent spec validation failed:", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_smoke_output_explains_blackbox_capability_limits(self):
        result = {
            "observation_mode": "blackbox",
            "process_lifecycle": {"started": True, "status": "process_completed"},
            "git_evidence": {},
            "trace_validation": {"valid": True},
            "adapter_capabilities": {"model_usage": False, "tool_trace": False, "workflow_trace": False},
            "scores": [{"evaluator": "test", "passed": True}],
            "evaluation": {"passed": True},
        }
        output = StringIO()
        with redirect_stdout(output):
            _print_smoke(Path(".runtime/example"), result)

        self.assertIn("Agent Smoke", output.getvalue())
        self.assertIn("— model usage: unsupported", output.getvalue())
        self.assertIn("Evaluation: PASS", output.getvalue())

    def test_smoke_output_reports_failed_stage_without_result_json(self):
        output = StringIO()
        with redirect_stdout(output):
            _print_smoke(Path(".runtime/example"), None)

        self.assertIn("Trial Artifact was not created", output.getvalue())

    def test_smoke_runs_a_real_blackbox_trial_through_existing_runner(self):
        with TemporaryDirectory() as directory:
            spec = Path(directory) / "agent.yaml"
            spec.write_text(json.dumps({
                "schema_version": 1,
                "agent": {"id": "blackbox-smoke", "version": "v1"},
                "runtime": {"command": [sys.executable, str(ROOT / "examples" / "external_blackbox_agent.py"), "--workspace", "{workspace}", "--task", "{task}"]},
                "observation": {"mode": "blackbox"},
            }), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "scripts/regression_lab.py", "agent", "smoke", str(spec), "--unsafe-trusted-host"],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIn("Agent Smoke", completed.stdout)
        self.assertIn("✓ Agent process completed", completed.stdout)
        self.assertIn("— model usage: unsupported", completed.stdout)
        self.assertIn("Evaluation: PASS", completed.stdout)

    def test_experiment_rejects_mismatched_agent_identity_version_or_observation_mode(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.yaml"
            candidate = root / "candidate.yaml"
            baseline.write_text(json.dumps({
                "schema_version": 1, "agent": {"id": "one", "version": "v1"},
                "runtime": {"command": [sys.executable]}, "observation": {"mode": "blackbox"},
            }), encoding="utf-8")
            candidate.write_text(json.dumps({
                "schema_version": 1, "agent": {"id": "two", "version": "v2"},
                "runtime": {"command": [sys.executable]}, "observation": {"mode": "blackbox"},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be the same"):
                _validate_experiment_specs(str(baseline), str(candidate))
            candidate.write_text(json.dumps({
                "schema_version": 1, "agent": {"id": "one", "version": "v1"},
                "runtime": {"command": [sys.executable]}, "observation": {"mode": "blackbox"},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be different"):
                _validate_experiment_specs(str(baseline), str(candidate))
            candidate.write_text(json.dumps({
                "schema_version": 1, "agent": {"id": "one", "version": "v2"},
                "runtime": {"command": [sys.executable]}, "observation": {"mode": "sdk"},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must match"):
                _validate_experiment_specs(str(baseline), str(candidate))

    def test_experiment_runs_real_blackbox_ab_and_records_agent_spec_snapshots(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            def write_spec(path: Path, version: str) -> None:
                path.write_text(json.dumps({
                    "schema_version": 1,
                    "agent": {"id": "blackbox-ab", "version": version},
                    "runtime": {"command": [sys.executable, str(ROOT / "examples" / "external_blackbox_agent.py"), "--workspace", "{workspace}", "--task", "{task}"]},
                    "observation": {"mode": "blackbox"},
                }), encoding="utf-8")
            baseline, candidate = root / "baseline.yaml", root / "candidate.yaml"
            write_spec(baseline, "v1")
            write_spec(candidate, "v2")
            completed = subprocess.run(
                [sys.executable, "scripts/regression_lab.py", "experiment", "run",
                 "--baseline", str(baseline), "--candidate", str(candidate),
                 "--benchmark", "benchmarks/smoke-case-design.yaml", "--trials", "3", "--unsafe-trusted-host"],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIn("Gate: INCONCLUSIVE", completed.stdout)
        runtime = Path(next(line.removeprefix("Runtime: ") for line in completed.stdout.splitlines() if line.startswith("Runtime: ")))
        protocol = json.loads((runtime / "protocol.json").read_text(encoding="utf-8"))
        snapshots = {item["label"]: item["agent_spec_snapshot"] for item in protocol["agents"]}
        self.assertEqual(set(snapshots), {"baseline", "candidate"})
        self.assertNotEqual(snapshots["baseline"]["agent_spec_hash"], snapshots["candidate"]["agent_spec_hash"])
        self.assertTrue(all(item["source_scope"] == "entrypoint_only" for item in snapshots.values()))
        report = json.loads((runtime / "experiment.json").read_text(encoding="utf-8"))
        self.assertEqual(sum(len(summary["jobs"]) for summary in report["summaries"].values()), 6)
        self.assertTrue(all(
            job["agent_source_hash_matches_protocol"]
            for summary in report["summaries"].values()
            for job in summary["jobs"]
        ))
        gate = json.loads((runtime / "gate-report.json").read_text(encoding="utf-8"))
        self.assertEqual(gate["decision"]["status"], "inconclusive")
        self.assertIn("average_model_tokens_limit", gate["decision"]["not_available"])
        self.assertIn("average_tool_calls_limit", gate["decision"]["not_available"])

    def test_console_reports_url_when_browser_cannot_open(self):
        output, errors = StringIO(), StringIO()
        with mock.patch("scripts.regression_lab._console_port", return_value=8765), \
             mock.patch("scripts.regression_lab.webbrowser.open", return_value=False), \
             mock.patch("scripts.regression_lab.subprocess.run", return_value=subprocess.CompletedProcess([], 0)):
            with redirect_stdout(output), redirect_stderr(errors):
                self.assertEqual(_serve_console(ROOT / ".runtime", None), 0)

        self.assertIn("Observability Console: http://127.0.0.1:", output.getvalue())
        self.assertIn("Open this URL in a browser:", output.getvalue())

    def test_console_stops_cleanly_on_keyboard_interrupt(self):
        with mock.patch("scripts.regression_lab._console_port", return_value=8765), \
             mock.patch("scripts.regression_lab.webbrowser.open", return_value=False), \
             mock.patch("scripts.regression_lab.subprocess.run", side_effect=KeyboardInterrupt):
            self.assertEqual(_serve_console(ROOT / ".runtime", None), 0)

    def test_console_rejects_explicit_busy_port(self):
        with mock.patch("scripts.regression_lab.socket.socket") as socket_factory:
            socket_factory.return_value.__enter__.return_value.bind.side_effect = OSError("busy")
            with self.assertRaisesRegex(ValueError, "already in use"):
                _console_port(8765)


if __name__ == "__main__":
    unittest.main()
