import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.attribution import aggregate_attribution, attribute_failure_span, attribute_trial


def result(*, status, passed=False, scores=()):
    return {"status": status, "evaluation": {"passed": passed}, "scores": list(scores)}


class FailureAttributionTests(unittest.TestCase):
    def test_primary_cause_uses_stable_precedence(self):
        policy_score = {"evaluator": "path_policy", "passed": False}
        self.assertEqual(attribute_trial(result(status="model_failed", scores=[policy_score]))["kind"], "model")
        self.assertEqual(attribute_trial(result(status="infra_failed", scores=[policy_score]))["kind"], "infrastructure")
        self.assertEqual(attribute_trial(result(status="trace_incomplete", scores=[policy_score]))["kind"], "evidence")
        self.assertEqual(attribute_trial(result(status="completed", scores=[policy_score]))["kind"], "policy")

    def test_agent_failure_is_distinct_from_external_failure(self):
        test_score = {"evaluator": "test", "passed": False}
        attributed = attribute_trial(result(status="agent_failed", scores=[test_score]))
        self.assertEqual(attributed["kind"], "agent")
        self.assertEqual(attributed["reason"], "task_test_failed_or_not_run")
        self.assertIsNone(attributed["failure_span"])

    def test_path_policy_locates_matching_tool_span(self):
        trace = [
            {"kind": "span_start", "event_seq": 1, "span_id": "span_018", "name": "tool.call", "span_type": "tool", "attributes": {"tool_name": "edit_file", "target_path": "secrets/config.py"}},
            {"kind": "span_end", "event_seq": 2, "span_id": "span_018", "status": "ok", "attributes": {}},
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text("\n".join(json.dumps(event) for event in trace), encoding="utf-8")
            result_value = result(status="completed", scores=[{"evaluator": "path_policy", "passed": False, "evidence": {"violating_files": ["secrets/config.py"]}}])
            result_value["trace_path"] = str(path)
            attributed = attribute_trial(result_value)

        self.assertEqual(attributed["failure_span"], {"span_id": "span_018", "span_type": "tool", "name": "tool.call", "tool_name": "edit_file"})
        self.assertEqual(attributed["evidence"], {"evaluator": "path_policy", "target_path": "secrets/config.py"})

    def test_test_and_model_failures_use_deterministic_terminal_spans(self):
        trace = [
            {"kind": "span_start", "event_seq": 1, "span_id": "test", "name": "test.run", "span_type": "test", "attributes": {}},
            {"kind": "span_end", "event_seq": 2, "span_id": "test", "status": "error", "attributes": {}},
            {"kind": "span_start", "event_seq": 3, "span_id": "model", "name": "model.call", "span_type": "llm", "attributes": {}},
            {"kind": "span_end", "event_seq": 4, "span_id": "model", "status": "error", "attributes": {}},
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text("\n".join(json.dumps(event) for event in trace), encoding="utf-8")
            test_result = result(status="agent_failed", scores=[{"evaluator": "test", "passed": False}])
            test_result["trace_path"] = str(path)
            model_result = result(status="model_failed")
            model_result["trace_path"] = str(path)
            test_attribution = attribute_failure_span(test_result)
            model_attribution = attribute_failure_span(model_result)

        self.assertEqual(test_attribution["failure_span"]["span_id"], "test")
        self.assertEqual(test_attribution["evidence"], {"evaluator": "test"})
        self.assertEqual(model_attribution["failure_span"]["span_id"], "model")
        self.assertEqual(model_attribution["evidence"], {"status": "model_failed"})

    def test_dual_reliability_excludes_only_model_and_infrastructure(self):
        jobs = [
            {"failure_attribution": {"kind": "passed", "reason": "valid_platform_evidence"}},
            {"failure_attribution": {"kind": "model", "reason": "model_provider_or_response_failure"}},
            {"failure_attribution": {"kind": "infrastructure", "reason": "runner_sandbox_or_deadline_failure"}},
            {"failure_attribution": {"kind": "policy", "reason": "path_policy_violation"}},
            {"failure_attribution": {"kind": "agent", "reason": "task_test_failed_or_not_run"}},
        ]
        summary = aggregate_attribution(jobs)
        self.assertEqual(summary["raw_reliability"]["valid_pass_rate"], 0.2)
        self.assertEqual(summary["agent_quality"]["eligible_trial_count"], 3)
        self.assertEqual(summary["agent_quality"]["valid_pass_rate"], 1 / 3)
        self.assertEqual(summary["counts"]["policy"], 1)


if __name__ == "__main__":
    unittest.main()
