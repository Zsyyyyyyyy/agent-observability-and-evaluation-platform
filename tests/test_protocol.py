import json
import os
import subprocess
import sys
import threading
import time
import unittest
from argparse import Namespace
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.protocol import build_execution_plan, build_protocol, compare_protocols, protocol_fingerprint
from scripts.run_experiment import _attempt_source_comparability, _freeze_or_restore_protocol, _run_execution_plan, describe_prompt_profiles, parse_external_arm_configs


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

    def test_frozen_protocol_keeps_the_original_case_timeout(self):
        with TemporaryDirectory() as directory:
            manifest = self._manifest(Path(directory))
            manifest["version"] = 1
            manifest["execution"]["timeout_seconds"] = 180
            protocol = build_protocol(
                manifests=[manifest], agents=[{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}],
                adapter="external-command", external_command=None, trials=1, use_docker=True, bash=False,
            )
            # 后续 Case 更新属于新实验口径，不能追溯改写已冻结 Runtime 的 Protocol。
            manifest["version"] = 2
            manifest["execution"]["timeout_seconds"] = 90

        snapshot = protocol["benchmark"]["cases"][0]
        self.assertEqual(snapshot["budget"]["timeout_seconds"], 180)
        self.assertEqual(snapshot["case_id"], "case")

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

    def test_protocol_freezes_external_agent_evidence_capabilities(self):
        with TemporaryDirectory() as directory:
            manifest = self._manifest(Path(directory))
            common = dict(
                manifests=[manifest], agents=[{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}],
                adapter="external-command", external_command=["python", "agent.py"], trials=3, use_docker=True, bash=False,
            )
            declared = {
                "schema_version": 2, "trace": True, "hierarchical_trace": True, "model_usage": True,
                "tool_trace": True, "tool_semantics": True, "test_trace": False, "context_trace": False,
                "workflow_trace": True, "mcp_trace": False,
            }
            before = build_protocol(**common, adapter_capabilities=declared)
            after = build_protocol(**common, adapter_capabilities={**declared, "workflow_trace": False})
        self.assertEqual(before["adapter_capabilities"], declared)
        self.assertEqual(compare_protocols(before, after), {"level": "not_comparable", "differences": ["adapter_capabilities"]})

    def test_protocol_preserves_per_arm_agent_spec_snapshots(self):
        with TemporaryDirectory() as directory:
            manifest = self._manifest(Path(directory))
            snapshot = {
                "agent_id": "demo", "version": "v1", "observation_mode": "blackbox",
                "normalized_command": ["python", "agent.py"], "capabilities": {"trace": True},
                "agent_spec_hash": "sha256:spec", "agent_source_hash": "sha256:source", "entrypoint_hash": "sha256:entrypoint",
                "source_scope": "entrypoint_only",
            }
            protocol = build_protocol(
                manifests=[manifest], agents=[{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}],
                adapter="external-command", external_command=None, trials=3, use_docker=True, bash=False,
                agent_snapshots={"baseline": snapshot},
            )
        baseline = next(item for item in protocol["agents"] if item["label"] == "baseline")
        self.assertEqual(baseline["agent_source_hash"], "sha256:source")
        self.assertEqual(baseline["agent_spec_snapshot"], snapshot)

    def test_per_arm_external_configs_keep_sdk_and_blackbox_capabilities_distinct(self):
        capabilities = {
            "schema_version": 2, "trace": True, "hierarchical_trace": True, "model_usage": True,
            "tool_trace": True, "tool_semantics": True, "test_trace": False, "context_trace": False,
            "workflow_trace": False, "mcp_trace": False,
        }
        agents = [{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}]
        configs = parse_external_arm_configs(json.dumps({
            "baseline": {"external_command": ["python", "baseline.py"], "adapter_capabilities": capabilities, "observation_mode": "sdk"},
            "candidate": {"external_command": ["python", "candidate.py"], "adapter_capabilities": capabilities, "observation_mode": "sdk"},
        }), agents)
        self.assertEqual(configs["baseline"]["external_command"], ["python", "baseline.py"])
        self.assertEqual(configs["candidate"]["observation_mode"], "sdk")

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

    def test_langgraph_experiment_does_not_require_sdk_protocol_handshake(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root)
            args = Namespace(
                report_only=False, external_observation_mode="langgraph", adapter="external-command",
                trials=1, bash=False, schedule_seed=20260816,
                comparison_intent="prompt_profile_only", allowed_differences=None,
                allow_protocol_mismatch=False,
            )
            protocol_state = _freeze_or_restore_protocol(
                args,
                agents=[{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}],
                manifests=[(Path(manifest["_manifest_path"]), manifest)], jobs=[{"trial_index": 1}],
                output_dir=root / "runtime", external_command=[sys.executable, str(ROOT / "examples" / "external_blackbox_agent.py")],
                external_arm_configs=None, adapter_capabilities=None, use_docker=False,
            )

        self.assertIsNotNone(protocol_state)
        assert protocol_state is not None
        protocol, _ = protocol_state
        self.assertTrue(all(item["rendered_prompt_set_hash"] == "unavailable" for item in protocol["agents"]))

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

    def test_execution_plan_freezes_pair_identity_order_and_concurrency(self):
        jobs = [
            {"case_id": "a", "trial_index": 1, "job_id": "a_trial_001"},
            {"case_id": "b", "trial_index": 1, "job_id": "b_trial_001"},
        ]
        agents = [{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}]

        plan = build_execution_plan(jobs, agents, seed=7, concurrency=2)

        self.assertEqual(plan["concurrency"], 2)
        self.assertEqual(plan["scheduling_policy"], "paired_baseline_then_candidate")
        self.assertEqual(len(plan["pairs"]), 2)
        for pair in plan["pairs"]:
            self.assertEqual(pair["agent_order"], ["baseline", "candidate"])
            entries = [entry for entry in plan["entries"] if entry["pair_id"] == pair["pair_id"]]
            self.assertEqual([entry["agent_label"] for entry in entries], ["baseline", "candidate"])

    def test_pair_scheduler_runs_pairs_in_parallel_but_versions_in_order(self):
        jobs = [
            {"case_id": "a", "trial_index": 1, "job_id": "a_trial_001"},
            {"case_id": "b", "trial_index": 1, "job_id": "b_trial_001"},
        ]
        agents = [{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}]
        plan = build_execution_plan(jobs, agents, seed=7, concurrency=2)
        events: list[tuple[str, str, str, float]] = []
        lock = threading.Lock()
        baseline_barrier = threading.Barrier(2)

        def fake_run(command, **_kwargs):
            version = command[command.index("--agent-version") + 1]
            case_id = Path(command[command.index("--output-dir") + 1]).name
            with lock:
                events.append((version, case_id, "start", time.monotonic()))
            if version == "v1":
                baseline_barrier.wait(timeout=2)
                time.sleep(0.02)
            with lock:
                events.append((version, case_id, "end", time.monotonic()))
            return Namespace(returncode=0, stdout="", stderr="")

        args = Namespace(adapter="react-agent", trials=1, replay_source=None, bash=False, resume=False)
        protocol = {"protocol_fingerprint": "sha256:test", "agents": []}
        with TemporaryDirectory() as directory, mock.patch("scripts.run_experiment.subprocess.run", side_effect=fake_run):
            returncode = _run_execution_plan(
                plan, agents=agents, manifests_by_id={"a": (Path("a.yaml"), {"id": "a"}), "b": (Path("b.yaml"), {"id": "b"})},
                output_dir=Path(directory), protocol=protocol, args=args, external_command=None,
                external_arm_configs=None, adapter_capabilities=None, use_docker=False,
            )

        self.assertEqual(returncode, 0)
        starts = {(version, case_id): stamp for version, case_id, event, stamp in events if event == "start"}
        ends = {(version, case_id): stamp for version, case_id, event, stamp in events if event == "end"}
        self.assertLess(starts[("v1", "a")], starts[("v2", "a")])
        self.assertLess(starts[("v1", "b")], starts[("v2", "b")])
        self.assertLess(starts[("v1", "a")], ends[("v1", "b")])
        self.assertLess(starts[("v1", "b")], ends[("v1", "a")])

    def test_pair_scheduler_isolates_infrastructure_failure_to_its_pair(self):
        jobs = [
            {"case_id": "a", "trial_index": 1, "job_id": "a_trial_001"},
            {"case_id": "b", "trial_index": 1, "job_id": "b_trial_001"},
        ]
        agents = [{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}]
        plan = build_execution_plan(jobs, agents, seed=7, concurrency=2)
        calls: list[tuple[str, str]] = []

        def fake_run(command, **_kwargs):
            version = command[command.index("--agent-version") + 1]
            case_id = Path(command[command.index("--output-dir") + 1]).name
            calls.append((version, case_id))
            return Namespace(returncode=2 if (version, case_id) == ("v1", "a") else 0, stdout="", stderr="")

        args = Namespace(adapter="react-agent", trials=1, replay_source=None, bash=False, resume=True)
        protocol = {"protocol_fingerprint": "sha256:test", "agents": []}
        with TemporaryDirectory() as directory, mock.patch("scripts.run_experiment.subprocess.run", side_effect=fake_run):
            returncode = _run_execution_plan(
                plan, agents=agents, manifests_by_id={"a": (Path("a.yaml"), {"id": "a"}), "b": (Path("b.yaml"), {"id": "b"})},
                output_dir=Path(directory), protocol=protocol, args=args, external_command=None,
                external_arm_configs=None, adapter_capabilities=None, use_docker=False,
            )

        self.assertEqual(returncode, 2)
        self.assertIn(("v1", "b"), calls)
        self.assertIn(("v2", "b"), calls)
        self.assertNotIn(("v2", "a"), calls)

    def test_concurrent_resume_reuses_completed_trial_artifacts(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            command = [
                sys.executable, "scripts/run_experiment.py", "--adapter", "failure-probe",
                "--agents", "baseline:failure-probe-v1,candidate:failure-probe-v2", "--trials", "1",
                "--concurrency", "2", "--unsafe-trusted-host", "--output-dir", str(output), "--resume",
                "--manifest", "benchmarks/failure-path-violation.yaml",
                "--manifest", "benchmarks/failure-unauthorized-tool.yaml",
            ]
            first = subprocess.run(command, cwd=ROOT, env={**os.environ, "PYTHONPATH": "src:."}, text=True, capture_output=True, check=False)
            before = {
                path.parent.relative_to(output).as_posix(): sorted(item.name for item in (path.parent / "attempts").iterdir())
                for path in output.rglob("selected-attempt.json")
            }
            resumed = subprocess.run(command, cwd=ROOT, env={**os.environ, "PYTHONPATH": "src:."}, text=True, capture_output=True, check=False)
            after = {
                path.parent.relative_to(output).as_posix(): sorted(item.name for item in (path.parent / "attempts").iterdir())
                for path in output.rglob("selected-attempt.json")
            }

        self.assertEqual(first.returncode, 1, first.stderr)
        self.assertEqual(resumed.returncode, 1, resumed.stderr)
        self.assertEqual(before, after)
        self.assertTrue(before)

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
