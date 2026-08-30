import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.integrity import verify_experiment_runtime
from regression_lab.gate import evaluate_gate
from regression_lab.protocol import protocol_fingerprint


class ExperimentIntegrityTests(unittest.TestCase):
    def _runtime(self, root: Path) -> tuple[Path, Path]:
        runtime = root / "experiment"
        job_id = "case_trial_001"
        job_dir = runtime / "baseline" / "case" / job_id
        attempt_dir = job_dir / "attempts" / "attempt_001"
        attempt_dir.mkdir(parents=True)
        trace_path = attempt_dir / "trace.jsonl"
        events = [
            {"trace_id": "trace_1", "event_seq": 1, "ts": 1.0, "kind": "span_start", "span_id": "root", "parent_span_id": None, "name": "agent.run", "attributes": {"trial_id": job_id}},
            {"trace_id": "trace_1", "event_seq": 2, "ts": 2.0, "kind": "span_end", "span_id": "root", "status": "ok", "attributes": {}},
        ]
        trace_path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

        protocol = {
            "schema_version": 2,
            "agents": [{
                "label": "baseline",
                "agent_source_hash": "sha256:source",
                "runtime_environment": {"identity_hash": "sha256:environment"},
            }],
        }
        protocol["protocol_fingerprint"] = protocol_fingerprint(protocol)
        (runtime / "protocol.json").write_text(json.dumps(protocol), encoding="utf-8")
        result = {
            "trial_id": job_id,
            "attempt_id": "attempt_001",
            "status": "completed",
            "trace_id": "trace_1",
            "trace_path": str(trace_path),
            "trace_validation": {"valid": True},
            "protocol_fingerprint": protocol["protocol_fingerprint"],
            "agent_source_hash": "sha256:source",
            "expected_agent_source_hash": "sha256:source",
            "agent_source_hash_matches_protocol": True,
            "runtime_environment": {"identity_hash": "sha256:environment"},
            "runtime_environment_matches_protocol": True,
        }
        attempt_result = attempt_dir / "result.json"
        attempt_result.write_text(json.dumps(result), encoding="utf-8")
        digest = "sha256:" + hashlib.sha256(attempt_result.read_bytes()).hexdigest()
        (attempt_dir / "attempt-manifest.json").write_text(json.dumps({
            "attempt_id": "attempt_001", "job_id": job_id, "status": "completed", "result_sha256": digest,
        }), encoding="utf-8")
        (job_dir / "selected-attempt.json").write_text(json.dumps({
            "attempt_id": "attempt_001", "job_id": job_id,
        }), encoding="utf-8")
        (job_dir / "result.json").write_text(json.dumps({**result, "attempt_path": str(attempt_dir)}), encoding="utf-8")

        summary_job = {
            "job_id": job_id, "case_id": "case", "trial_index": 1,
            **{field: result[field] for field in (
                "status", "trace_id", "agent_source_hash", "expected_agent_source_hash",
                "agent_source_hash_matches_protocol",
            )},
        }
        experiment = {
            "protocol": {"fingerprint": protocol["protocol_fingerprint"], "comparability": {"level": "strict"}},
            "summaries": {"baseline": {"job_count": 1, "jobs": [summary_job]}},
            "comparison": {"baseline": {}, "candidate": {}},
        }
        (runtime / "experiment.json").write_text(json.dumps(experiment), encoding="utf-8")
        (runtime / "execution-plan.json").write_text(json.dumps({"entries": [{
            "agent_label": "baseline", "job_id": job_id, "case_id": "case", "trial_index": 1,
        }]}), encoding="utf-8")
        (runtime / "gate-report.json").write_text(json.dumps({
            "evidence": {"comparison": experiment["comparison"]},
        }), encoding="utf-8")
        return runtime, attempt_result

    def test_verifies_complete_selected_attempt_evidence_chain(self):
        with TemporaryDirectory() as directory:
            runtime, _ = self._runtime(Path(directory))

            report = verify_experiment_runtime(runtime)

        self.assertTrue(report["valid"], report["issues"])
        self.assertEqual(report["trial_count"], 1)

    def test_detects_tampered_immutable_attempt_result(self):
        with TemporaryDirectory() as directory:
            runtime, attempt_result = self._runtime(Path(directory))
            value = json.loads(attempt_result.read_text(encoding="utf-8"))
            value["status"] = "agent_failed"
            attempt_result.write_text(json.dumps(value), encoding="utf-8")

            report = verify_experiment_runtime(runtime)

        self.assertFalse(report["valid"])
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("attempt_result_digest_mismatch", codes)
        self.assertIn("selected_result_projection_mismatch", codes)

    def test_rejects_job_identity_that_escapes_the_runtime(self):
        with TemporaryDirectory() as directory:
            runtime, _ = self._runtime(Path(directory))
            experiment_path = runtime / "experiment.json"
            experiment = json.loads(experiment_path.read_text(encoding="utf-8"))
            experiment["summaries"]["baseline"]["jobs"][0]["case_id"] = "../../outside"
            experiment_path.write_text(json.dumps(experiment), encoding="utf-8")

            report = verify_experiment_runtime(runtime)

        self.assertFalse(report["valid"])
        self.assertIn("job_path_invalid", {issue["code"] for issue in report["issues"]})

    def test_detects_tampered_runtime_environment_identity(self):
        with TemporaryDirectory() as directory:
            runtime, attempt_result = self._runtime(Path(directory))
            value = json.loads(attempt_result.read_text(encoding="utf-8"))
            value["runtime_environment"] = {"identity_hash": "sha256:other"}
            attempt_result.write_text(json.dumps(value), encoding="utf-8")
            attempt_dir = attempt_result.parent
            manifest_path = attempt_dir / "attempt-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["result_sha256"] = "sha256:" + hashlib.sha256(attempt_result.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            published_path = attempt_dir.parent.parent / "result.json"
            published_path.write_text(json.dumps({**value, "attempt_path": str(attempt_dir)}), encoding="utf-8")

            report = verify_experiment_runtime(runtime)

        self.assertFalse(report["valid"])
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("trial_runtime_environment_mismatch", codes)

    def test_detects_tampered_gate_decision_when_policy_snapshot_is_available(self):
        with TemporaryDirectory() as directory:
            runtime, _ = self._runtime(Path(directory))
            experiment_path = runtime / "experiment.json"
            experiment = json.loads(experiment_path.read_text(encoding="utf-8"))
            metrics = {
                "completion_rate": 1.0,
                "evaluation_pass_rate": 1.0,
                "model_failed_rate": 0.0,
                "trace_incomplete_rate": 0.0,
                "environment_mismatch_rate": 0.0,
                "infra_failed_rate": 0.0,
                "path_policy_violation_rate": 0.0,
                "diff_policy_violation_rate": 0.0,
                "avg_duration_ms": 1.0,
                "avg_tool_calls": 1.0,
                "avg_model_tokens": 1.0,
            }
            experiment["comparison"] = {"baseline": metrics, "candidate": metrics, "case_comparisons": []}
            experiment_path.write_text(json.dumps(experiment), encoding="utf-8")
            gate = evaluate_gate(experiment, {})
            gate["decision"]["status"] = "promote"
            gate["passed"] = True
            (runtime / "gate-report.json").write_text(json.dumps(gate), encoding="utf-8")

            report = verify_experiment_runtime(runtime)

        self.assertFalse(report["valid"])
        self.assertIn("gate_decision_mismatch", {issue["code"] for issue in report["issues"]})


if __name__ == "__main__":
    unittest.main()
