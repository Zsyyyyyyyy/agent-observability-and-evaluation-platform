import unittest

from regression_lab.gate import evaluate_gate


def experiment(baseline, candidate):
    return {"baseline_id": "baseline", "candidate_id": "candidate", "comparison": {"baseline": baseline, "candidate": candidate}}


METRICS = {
    "completion_rate": 1.0, "evaluation_pass_rate": 1.0, "model_failed_rate": 0.0,
    "trace_incomplete_rate": 0.0, "infra_failed_rate": 0.0, "avg_duration_ms": 100.0,
    "avg_tool_calls": 5.0, "avg_model_tokens": 1000.0,
}


class GateTests(unittest.TestCase):
    def test_default_policy_accepts_non_regressing_candidate(self):
        candidate = {**METRICS, "avg_duration_ms": 90.0, "avg_tool_calls": 5.4, "avg_model_tokens": 1050.0}
        report = evaluate_gate(experiment(METRICS, candidate), {})
        self.assertTrue(report["passed"])

    def test_gate_rejects_reliability_and_correctness_regression(self):
        candidate = {**METRICS, "completion_rate": 0.9, "model_failed_rate": 0.1}
        report = evaluate_gate(experiment(METRICS, candidate), {})
        failed = {rule["name"] for rule in report["rules"] if not rule["passed"]}
        self.assertIn("completion_rate_non_regression", failed)
        self.assertIn("model_failed_rate_non_regression", failed)

    def test_gate_rejects_cost_above_tolerance(self):
        candidate = {**METRICS, "avg_tool_calls": 5.6, "avg_model_tokens": 1200.0}
        report = evaluate_gate(experiment(METRICS, candidate), {})
        failed = {rule["name"] for rule in report["rules"] if not rule["passed"]}
        self.assertIn("average_tool_calls_limit", failed)
        self.assertIn("average_model_tokens_limit", failed)
