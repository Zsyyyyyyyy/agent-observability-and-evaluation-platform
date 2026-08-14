import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.dashboard import DashboardRepository
from regression_lab.evolution import evaluation_context_hash


class DashboardRepositoryTests(unittest.TestCase):
    def test_aggregates_trials_and_blocks_path_escape(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); trial = root / "baseline" / "case"; trial.mkdir(parents=True)
            trace = trial / "trace.jsonl"; trace.write_text('{"kind":"event","name":"agent.stop"}\n', encoding="utf-8")
            result = {"trial_id": "case_trial_001", "status": "completed", "agent_version": "v1", "trace_path": str(trace), "evaluation": {"passed": True}, "failure_attribution": {"kind": "passed", "reason": "valid_platform_evidence"}, "model_usage": {"total_tokens": 12}, "behavior": {"tool_success_rate": 1.0, "tool_error_rate": 0.0, "denied_tool_attempts": 0, "availability": {"tool_outcomes": True}}, "scores": [{"evaluator": "budget", "actual": {"duration_ms": 10}}, {"evaluator": "tool_integrity", "actual": {"tool_calls": 2}}]}
            (trial / "result.json").write_text(json.dumps(result), encoding="utf-8")
            nested = trial / "attempts" / "attempt_001"; nested.mkdir(parents=True)
            (nested / "result.json").write_text(json.dumps({**result, "status": "trace_incomplete"}), encoding="utf-8")
            archived = root / "invalid-attempts" / "attempt"; archived.mkdir(parents=True)
            (archived / "result.json").write_text(json.dumps({**result, "status": "trace_incomplete"}), encoding="utf-8")
            repo = DashboardRepository(root)

            dashboard = repo.dashboard()
            self.assertEqual(dashboard["pass_rate"], 1.0)
            self.assertEqual(dashboard["trace_incomplete_count"], 0)
            self.assertEqual(dashboard["trial_count"], 1)
            self.assertEqual(dashboard["runtime_label"], f"Local experiment artifacts · {root.name}")
            self.assertEqual(dashboard["behavior"]["tool_success_rate"], 1.0)
            self.assertNotIn(str(root), dashboard["runtime_label"])
            self.assertEqual(repo.trials()[0]["case_id"], "case")
            self.assertEqual(repo.trials()[0]["failure_reason"], "valid_platform_evidence")
            self.assertEqual(repo.trials()[0]["id"], "baseline/case")
            self.assertEqual(repo.trial("baseline/case")["trace"][0]["name"], "agent.stop")
            self.assertIsNone(repo.trial("../../etc"))

    def test_policy_stop_evidence_requires_stop_before_no_more_calls(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); trial = root / "candidate" / "case_trial_001"; trial.mkdir(parents=True)
            trace = trial / "trace.jsonl"
            trace.write_text("\n".join([
                json.dumps({"event_seq": 1, "kind": "span_start", "name": "model.call"}),
                json.dumps({"event_seq": 2, "kind": "event", "name": "agent.stop", "attributes": {"reason": "verification_passed_policy"}}),
                json.dumps({"event_seq": 3, "kind": "span_end", "name": "agent.run"}),
            ]), encoding="utf-8")
            result = {"trial_id": "case_trial_001", "agent_profile": "bounded-success-stop-verify-v4-1", "agent_exit_reason": "verification_passed", "trace_path": str(trace)}
            (trial / "result.json").write_text(json.dumps(result), encoding="utf-8")
            evidence = DashboardRepository(root).policy_stop_evidence()

        self.assertTrue(evidence["available"])
        self.assertEqual(evidence["policy_stop_trace_count"], 1)
        self.assertEqual(evidence["post_stop_model_or_tool_spans"], 0)
        self.assertEqual(evidence["missing_policy_stop_count"], 0)

    def test_latest_gate_reads_negative_control_decision(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "gate-negative.json").write_text(json.dumps({"decision": {"status": "hold"}}), encoding="utf-8")

            gate = DashboardRepository(root).latest_gate()

        self.assertEqual(gate, {"decision": {"status": "hold"}})

    def test_evolution_limits_timeline_to_open_experiment(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "experiment"; root.mkdir()
            catalog = root.parent / "evolution-catalog.json"
            context = {"case_ids": ["case"]}
            catalog.write_text(json.dumps({
                "schema_version": 1,
                "agents": [{"agent_id": "agent", "display_name": "Agent", "kind": "external", "created_at": "2026-08-12T12:00:00Z", "metadata": {}}],
                "versions": [
                    {"version_id": "v1", "agent_id": "agent", "version": "v1", "parent_version_id": None, "status": "champion", "change_type": "config", "change_summary": "base", "created_at": "2026-08-12T12:00:00Z", "snapshot": {"adapter_id": "external-command", "model": "m", "prompt_profile": "p", "toolset_hash": "sha256:t", "config_hash": "sha256:c"}},
                    {"version_id": "v2", "agent_id": "agent", "version": "v2", "parent_version_id": "v1", "status": "candidate", "change_type": "prompt", "change_summary": "prompt", "created_at": "2026-08-12T12:00:00Z", "snapshot": {"adapter_id": "external-command", "model": "m", "prompt_profile": "p2", "toolset_hash": "sha256:t", "config_hash": "sha256:c"}},
                    {"version_id": "v3", "agent_id": "agent", "version": "v3", "parent_version_id": "v2", "status": "candidate", "change_type": "prompt", "change_summary": "prompt 2", "created_at": "2026-08-13T12:00:00Z", "snapshot": {"adapter_id": "external-command", "model": "m", "prompt_profile": "p3", "toolset_hash": "sha256:t", "config_hash": "sha256:c"}},
                ],
                "cases": [{"case_id": "case", "manifest_id": "case", "manifest_version": 1, "fixture_hash": "sha256:f", "test_hash": "sha256:t", "policy_hash": "sha256:p"}],
                "experiments": [
                    {"experiment_id": "exp-1", "name": "v1 vs v2", "baseline_version_id": "v1", "candidate_version_id": "v2", "status": "completed", "created_at": "2026-08-12T12:00:00Z", "completed_at": "2026-08-12T12:00:00Z", "case_ids": ["case"], "evaluation_context": context, "evaluation_context_hash": evaluation_context_hash(context), "evaluator_version": "v2", "gate_policy_version": "g", "artifact_root": "x", "comparison_basis": {"case_ids": ["case"], "evaluator_version": "v2"}, "comparison_basis_hash": "sha256:same", "comparison_summary": {"delta": {"evaluation_pass_rate": 0.1}}},
                    {"experiment_id": "exp-2", "name": "v2 vs v3", "baseline_version_id": "v2", "candidate_version_id": "v3", "status": "completed", "created_at": "2026-08-13T12:00:00Z", "completed_at": "2026-08-13T12:00:00Z", "case_ids": ["case"], "evaluation_context": context, "evaluation_context_hash": evaluation_context_hash(context), "evaluator_version": "v2", "gate_policy_version": "g", "artifact_root": "y", "comparison_basis": {"case_ids": ["case"], "evaluator_version": "v2"}, "comparison_basis_hash": "sha256:same", "comparison_summary": {"delta": {"evaluation_pass_rate": 0.2, "avg_duration_ms": -100}}},
                ],
                "trials": [], "attempts": [],
                "gate_decisions": [{"gate_id": "g1", "experiment_id": "exp-1", "status": "promote", "policy_version": "g", "decided_at": "2026-08-12T12:00:00Z", "rules": [], "evidence": {"report": "x"}}],
            }), encoding="utf-8")
            (root / "experiment.json").write_text(json.dumps({"evolution_experiment_id": "exp-2", "evolution_catalog": str(catalog)}), encoding="utf-8")
            timeline = DashboardRepository(root).evolution()

        self.assertTrue(timeline["available"])
        self.assertEqual([item["version"] for item in timeline["versions"]], ["v1", "v2", "v3"])
        self.assertEqual(len(timeline["experiments"]), 2)
        self.assertEqual(timeline["current_experiment_id"], "exp-2")
        self.assertEqual(timeline["experiments"][1]["comparability"]["level"], "strict")
        self.assertEqual(timeline["gate_decisions"][0]["status"], "promote")
