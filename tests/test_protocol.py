import json
import os
import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.protocol import build_execution_plan, build_protocol, compare_protocols, protocol_fingerprint
from scripts.run_experiment import _attempt_source_comparability, describe_prompt_profiles


ROOT = Path(__file__).resolve().parents[1]


class ProtocolTests(unittest.TestCase):
    def _manifest(self, root: Path) -> dict:
        fixture = root / "fixtures" / "case"; fixture.mkdir(parents=True)
        (fixture / "app.py").write_text("value = 1\n", encoding="utf-8")
        manifest = {
            "id": "case", "version": 1, "_manifest_path": str(root / "benchmarks" / "case.yaml"),
            "fixture": {"path": "fixtures/case", "test_command": "python -m unittest"},
            "execution": {"max_tokens": 1000, "max_tool_calls": 5, "timeout_seconds": 30},
            "tool_policy": {"allow": ["read_file"], "deny": ["shell"]},
        }
        Path(manifest["_manifest_path"]).parent.mkdir()
        return manifest

    def test_protocol_is_stable_and_never_persists_api_key(self):
        with TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"AGENT_API_KEY": "secret-value", "AGENT_MODEL": "demo-model"}, clear=False):
            manifest = self._manifest(Path(directory))
            first = build_protocol(manifests=[manifest], agents=[{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}], adapter="external-command", external_command=["python", "missing-agent.py"], trials=3, use_docker=True, bash=False)
            second = build_protocol(manifests=[manifest], agents=[{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}], adapter="external-command", external_command=["python", "missing-agent.py"], trials=3, use_docker=True, bash=False)
        self.assertEqual(first["protocol_fingerprint"], second["protocol_fingerprint"])
        self.assertEqual(first["protocol_fingerprint"], protocol_fingerprint(first))
        self.assertNotIn("secret-value", json.dumps(first))

    def test_protocol_marks_model_change_not_comparable(self):
        with TemporaryDirectory() as directory:
            manifest = self._manifest(Path(directory))
            with mock.patch.dict(os.environ, {"AGENT_MODEL": "model-a"}, clear=False):
                before = build_protocol(manifests=[manifest], agents=[{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}], adapter="react-agent", external_command=None, trials=3, use_docker=True, bash=False)
            with mock.patch.dict(os.environ, {"AGENT_MODEL": "model-b"}, clear=False):
                after = build_protocol(manifests=[manifest], agents=[{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}], adapter="react-agent", external_command=None, trials=3, use_docker=True, bash=False)
        self.assertEqual(compare_protocols(before, after), {"level": "not_comparable", "differences": ["model"]})

    def test_protocol_marks_intervention_definition_change_not_comparable(self):
        with TemporaryDirectory() as directory:
            manifest = self._manifest(Path(directory))
            common = dict(manifests=[manifest], agents=[{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}], adapter="react-agent", external_command=None, trials=3, use_docker=True, bash=False)
            before = build_protocol(**common, comparison_intent="prompt_profile_only")
            after = build_protocol(**common, comparison_intent="runtime_policy", allowed_differences=("agents[].runtime_policy",))
        self.assertEqual(compare_protocols(before, after), {"level": "not_comparable", "differences": ["comparison_intent", "allowed_differences"]})

    def test_protocol_freezes_explicit_sampling_defaults_and_rendered_prompt_hashes(self):
        with TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {"AGENT_TEMPERATURE": "", "AGENT_TOP_P": "", "AGENT_SEED": ""},
            clear=False,
        ):
            root = Path(directory)
            manifest = self._manifest(root)
            command = [sys.executable, str(ROOT / "examples" / "external_openai_agent.py")]
            agents = [{"id": "baseline", "version": "external-openai-v2"}, {"id": "candidate", "version": "external-openai-v3"}]
            profiles = describe_prompt_profiles(command, agents, [manifest])
            protocol = build_protocol(
                manifests=[manifest], agents=agents, adapter="external-command", external_command=command,
                trials=3, use_docker=True, bash=False, prompt_profiles=profiles,
            )
        self.assertEqual(protocol["schema_version"], 2)
        self.assertEqual(protocol["model"]["temperature"], 0.0)
        self.assertEqual(protocol["model"]["top_p"], 1.0)
        self.assertEqual(protocol["model"]["seed"], "not_configured")
        hashes = [item["rendered_prompt_set_hash"] for item in protocol["agents"]]
        self.assertTrue(all(value.startswith("sha256:") for value in hashes))
        self.assertNotEqual(hashes[0], hashes[1])

    def test_protocol_rejects_invalid_sampling_configuration(self):
        with TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"AGENT_TEMPERATURE": "not-a-number"}, clear=False):
            manifest = self._manifest(Path(directory))
            with self.assertRaisesRegex(ValueError, "AGENT_TEMPERATURE"):
                build_protocol(manifests=[manifest], agents=[{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}], adapter="react-agent", external_command=None, trials=3, use_docker=True, bash=False)

    def test_attempt_source_hash_mismatch_is_not_comparable(self):
        protocol = {"agents": [{"label": "candidate", "agent_source_hash": "sha256:frozen"}]}
        agents = [{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}]
        summaries = {"baseline": {"jobs": [{}]}, "candidate": {"jobs": [{"job_id": "case_trial_001", "agent_source_hash": "sha256:changed"}]}}
        self.assertEqual(
            _attempt_source_comparability(protocol, agents, summaries),
            {"level": "not_comparable", "differences": ["attempt_agent_source_hash"], "mismatched_attempts": ["candidate:case_trial_001"]},
        )

    def test_attempt_source_hash_match_keeps_strict_comparability(self):
        protocol = {"agents": [{"label": "candidate", "agent_source_hash": "sha256:frozen"}]}
        agents = [{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}]
        summaries = {"baseline": {"jobs": [{}]}, "candidate": {"jobs": [{"job_id": "case_trial_001", "agent_source_hash": "sha256:frozen"}]}}
        self.assertEqual(_attempt_source_comparability(protocol, agents, summaries), {"level": "strict", "differences": []})

    def test_execution_plan_is_paired_interleaved_and_repeatable(self):
        jobs = [{"case_id": "a", "trial_index": 1, "job_id": "a_trial_001"}, {"case_id": "b", "trial_index": 1, "job_id": "b_trial_001"}]
        agents = [{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}]
        first = build_execution_plan(jobs, agents, seed=7)
        self.assertEqual(first, build_execution_plan(jobs, agents, seed=7))
        self.assertEqual([item["agent_label"] for item in first["entries"]].count("baseline"), 2)
        self.assertEqual([item["agent_label"] for item in first["entries"]].count("candidate"), 2)
        self.assertEqual([item["schedule_index"] for item in first["entries"]], [1, 2, 3, 4])

    def test_execution_plan_supports_three_arms_without_losing_trial_pairing(self):
        jobs = [{"case_id": "a", "trial_index": index, "job_id": f"a_trial_{index:03d}"} for index in range(1, 4)]
        agents = [{"id": "champion", "version": "v3"}, {"id": "positive", "version": "v4.1"}, {"id": "negative", "version": "v4-negative"}]
        plan = build_execution_plan(jobs, agents, seed=7)
        self.assertEqual(len(plan["entries"]), 9)
        for trial_index in range(1, 4):
            labels = [entry["agent_label"] for entry in plan["entries"] if entry["trial_index"] == trial_index]
            self.assertEqual(set(labels), {"champion", "positive", "negative"})

    def test_experiment_refuses_resume_when_protocol_changes(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            command = [
                sys.executable, "scripts/run_experiment.py", "--adapter", "failure-probe",
                "--agents", "baseline:failure-probe-v1,candidate:failure-probe-v2", "--trials", "1",
                "--unsafe-trusted-host", "--output-dir", str(output), "--resume",
                "--manifest", "benchmarks/failure-path-violation.yaml",
            ]
            first = subprocess.run(command, cwd=ROOT, env={**os.environ, "PYTHONPATH": "src:."}, text=True, capture_output=True, check=False)
            self.assertEqual(first.returncode, 1, first.stderr)
            protocol = json.loads((output / "protocol.json").read_text(encoding="utf-8"))
            plan = json.loads((output / "execution-plan.json").read_text(encoding="utf-8"))
            report = json.loads((output / "experiment.json").read_text(encoding="utf-8"))
            self.assertEqual(len(plan["entries"]), 2)
            self.assertEqual(report["protocol"]["fingerprint"], protocol["protocol_fingerprint"])
            result_paths = [path for path in output.rglob("result.json") if "attempts" not in path.parts]
            self.assertEqual({json.loads(path.read_text(encoding="utf-8"))["protocol_fingerprint"] for path in result_paths}, {protocol["protocol_fingerprint"]})

            changed = subprocess.run([*command, "--schedule-seed", "99"], cwd=ROOT, env={**os.environ, "PYTHONPATH": "src:."}, text=True, capture_output=True, check=False)
            self.assertEqual(changed.returncode, 2)
            self.assertIn("PROTOCOL MISMATCH", changed.stderr)

    def test_report_only_preserves_persisted_strict_comparability(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            command = [
                sys.executable, "scripts/run_experiment.py", "--adapter", "failure-probe",
                "--agents", "baseline:failure-probe-v1,candidate:failure-probe-v2", "--trials", "1",
                "--unsafe-trusted-host", "--output-dir", str(output), "--resume",
                "--manifest", "benchmarks/failure-path-violation.yaml",
            ]
            first = subprocess.run(command, cwd=ROOT, env={**os.environ, "PYTHONPATH": "src:."}, text=True, capture_output=True, check=False)
            self.assertEqual(first.returncode, 1, first.stderr)
            initial_report = json.loads((output / "experiment.json").read_text(encoding="utf-8"))
            self.assertEqual(initial_report["protocol"]["comparability"]["level"], "strict")
            rebuilt = subprocess.run([*command, "--report-only"], cwd=ROOT, env={**os.environ, "PYTHONPATH": "src:."}, text=True, capture_output=True, check=False)
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
            report = json.loads((output / "experiment.json").read_text(encoding="utf-8"))
            self.assertEqual(report["protocol"]["comparability"]["level"], "strict")


if __name__ == "__main__":
    unittest.main()
