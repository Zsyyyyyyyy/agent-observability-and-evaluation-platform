import unittest
import math

from regression_lab.gate import evaluate_gate
from scripts.evaluate_gate import select_comparison_arm


def experiment(baseline, candidate):
    cases = [
        {
            "case_id": f"case-{index}",
            "baseline": {"trial_count": 3},
            "candidate": {"trial_count": 3},
            "paired_trial_count": 3,
        }
        for index in range(8)
    ]
    reliability = {
        "baseline": {"all_pass_at_k": 1.0, "flaky_case_rate": 0.0},
        "candidate": {"all_pass_at_k": 1.0, "flaky_case_rate": 0.0},
    }
    return {
        "baseline_id": "baseline",
        "candidate_id": "candidate",
        "trial_count_required_per_case": 3,
        "protocol": {"fingerprint": "sha256:strict", "comparability": {"level": "strict", "differences": []}},
        "comparison": {"baseline": baseline, "candidate": candidate, "case_comparisons": cases, "reliability": reliability},
    }


METRICS = {
    "completion_rate": 1.0, "evaluation_pass_rate": 1.0, "model_failed_rate": 0.0,
    "trace_incomplete_rate": 0.0, "infra_failed_rate": 0.0, "avg_duration_ms": 100.0,
    "path_policy_violation_rate": 0.0, "diff_policy_violation_rate": 0.0,
    "avg_tool_calls": 5.0, "avg_model_tokens": 1000.0,
}


class GateTests(unittest.TestCase):
    def test_selects_one_multi_arm_candidate_for_independent_gate_evaluation(self):
        champion_summary = {"jobs": []}
        positive_summary = {"jobs": []}
        negative_summary = {"jobs": []}
        comparison = {"baseline": {}, "candidate": {}, "delta": {}, "case_comparisons": []}
        document = {
            "agents": [{"id": "champion"}, {"id": "positive"}, {"id": "negative"}],
            "summaries": {"champion": champion_summary, "positive": positive_summary, "negative": negative_summary},
            "primary_comparison_id": "positive",
            "comparison_arms": {
                "positive": {"baseline_id": "champion", "candidate_id": "positive", "comparison": comparison},
                "negative": {"baseline_id": "champion", "candidate_id": "negative", "comparison": comparison},
            },
            "evolution_experiment_ids": {"positive": "exp_positive", "negative": "exp_negative"},
        }
        selected, arm_id = select_comparison_arm(document, "negative")
        self.assertEqual(arm_id, "negative")
        self.assertEqual(selected["candidate_id"], "negative")
        self.assertEqual(selected["evolution_experiment_id"], "exp_negative")
        self.assertEqual([agent["id"] for agent in selected["agents"]], ["champion", "negative"])
    def test_default_policy_accepts_non_regressing_candidate(self):
        candidate = {**METRICS, "avg_duration_ms": 90.0, "avg_tool_calls": 5.4, "avg_model_tokens": 1050.0}
        report = evaluate_gate(experiment(METRICS, candidate), {})
        self.assertTrue(report["passed"])

    def test_latency_is_diagnostic_instead_of_a_hard_block(self):
        candidate = {**METRICS, "avg_duration_ms": 180.0, "avg_tool_calls": 5.0, "avg_model_tokens": 1000.0}
        report_input = experiment(METRICS, candidate)
        report_input["comparison"] = {
                **report_input["comparison"],
                "baseline": METRICS, "candidate": candidate,
                "efficiency": {
                    "baseline": {"p50_duration_ms": 100.0, "p95_duration_ms": 120.0},
                    "candidate": {"p50_duration_ms": 140.0, "p95_duration_ms": 260.0},
                },
        }
        report = evaluate_gate(report_input, {})
        self.assertTrue(report["passed"])
        self.assertNotIn("average_duration_non_regression", {rule["name"] for rule in report["rules"]})
        self.assertEqual(report["diagnostics"]["p95_duration_ms"]["status"], "regressed")
        self.assertFalse(report["diagnostics"]["p95_duration_ms"]["blocking"])

    def test_token_savings_cannot_offset_correctness_regression(self):
        candidate = {**METRICS, "completion_rate": 0.9, "evaluation_pass_rate": 0.9, "avg_model_tokens": 500.0}
        report = evaluate_gate(experiment(METRICS, candidate), {})
        self.assertFalse(report["passed"])
        self.assertTrue(report["decision"]["correctness_or_reliability_regressed"])
        self.assertTrue(report["decision"]["token_savings_cannot_offset"])
        self.assertIn("not offset by token savings", report["decision"]["message"])

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

    def test_gate_rejects_policy_violations_and_repeatability_regression(self):
        candidate = {**METRICS, "path_policy_violation_rate": 0.1}
        report_input = experiment({**METRICS, "path_policy_violation_rate": 0.0, "diff_policy_violation_rate": 0.0}, candidate)
        report_input["comparison"] = {
                **report_input["comparison"],
                "baseline": {**METRICS, "path_policy_violation_rate": 0.0, "diff_policy_violation_rate": 0.0},
                "candidate": candidate,
                "reliability": {"baseline": {"all_pass_at_k": 1.0, "flaky_case_rate": 0.0}, "candidate": {"all_pass_at_k": 0.5, "flaky_case_rate": 0.5}},
        }
        report = evaluate_gate(report_input, {})
        failed = {rule["name"] for rule in report["rules"] if not rule["passed"]}
        self.assertIn("candidate_path_policy_violation_rate_limit", failed)
        self.assertIn("all_pass_at_3_non_regression", failed)
        self.assertIn("flaky_case_rate_non_regression", failed)

    def test_gate_allows_empty_diff_symptom_when_policy_rate_is_zero(self):
        candidate = {**METRICS, "path_policy_violation_rate": 0.0, "diff_policy_violation_rate": 0.0}
        report = evaluate_gate(experiment({**METRICS, "path_policy_violation_rate": 0.0, "diff_policy_violation_rate": 0.0}, candidate), {})
        self.assertTrue(next(rule["passed"] for rule in report["rules"] if rule["name"] == "candidate_diff_policy_violation_rate_limit"))

    def test_protocol_aware_gate_requires_strict_comparability_for_promotion(self):
        strict = evaluate_gate(experiment(METRICS, METRICS), {})
        self.assertTrue(strict["passed"])
        self.assertEqual(strict["decision"]["status"], "promote")

        mixed = evaluate_gate({**experiment(METRICS, METRICS), "protocol": {"fingerprint": "sha256:mixed", "comparability": {"level": "not_comparable", "differences": ["model"]}}}, {})
        self.assertFalse(mixed["passed"])
        self.assertEqual(mixed["decision"]["status"], "inconclusive")
        self.assertIn("protocol_strict_comparability", mixed["decision"]["hard_blocking_failures"])

    def test_gate_blocks_new_cost_when_zero_baseline_makes_ratio_undefined(self):
        baseline = {**METRICS, "avg_tool_calls": 0.0, "avg_model_tokens": 0.0}
        candidate = {**baseline, "avg_tool_calls": 1.0, "avg_model_tokens": 20.0}
        report = evaluate_gate(experiment(baseline, candidate), {})
        failed = {rule["name"]: rule for rule in report["rules"] if not rule["passed"]}
        self.assertIn("average_tool_calls_limit", failed)
        self.assertIn("average_model_tokens_limit", failed)
        self.assertIn("zero baseline", failed["average_tool_calls_limit"]["expected"])

    def test_gate_can_explicitly_allow_small_absolute_cost_on_zero_baseline(self):
        baseline = {**METRICS, "avg_tool_calls": 0.0, "avg_model_tokens": 0.0}
        candidate = {**baseline, "avg_tool_calls": 1.0, "avg_model_tokens": 20.0}
        report = evaluate_gate(experiment(baseline, candidate), {
            "max_avg_tool_calls_absolute_increase_when_baseline_zero": 1.0,
            "max_avg_model_tokens_absolute_increase_when_baseline_zero": 20.0,
        })
        self.assertTrue(report["passed"])

    def test_gate_holds_when_both_versions_have_zero_completion(self):
        failed = {**METRICS, "completion_rate": 0.0, "evaluation_pass_rate": 0.0, "model_failed_rate": 1.0}
        report = evaluate_gate(experiment(failed, failed), {})
        self.assertFalse(report["passed"])
        self.assertEqual(report["decision"]["status"], "hold")
        blocked = set(report["decision"]["hard_blocking_failures"])
        self.assertIn("candidate_completion_rate_minimum", blocked)
        self.assertIn("candidate_model_failed_rate_limit", blocked)

    def test_gate_holds_when_both_versions_have_total_infrastructure_failure(self):
        failed = {**METRICS, "completion_rate": 0.0, "evaluation_pass_rate": 0.0, "infra_failed_rate": 1.0}
        report = evaluate_gate(experiment(failed, failed), {})
        self.assertEqual(report["decision"]["status"], "hold")
        self.assertIn("candidate_infra_failed_rate_limit", report["decision"]["hard_blocking_failures"])

    def test_gate_rejects_non_finite_or_impossible_persisted_metrics(self):
        with self.assertRaisesRegex(ValueError, "finite rate between 0 and 1"):
            evaluate_gate(experiment(METRICS, {**METRICS, "completion_rate": math.nan}), {})
        with self.assertRaisesRegex(ValueError, "finite non-negative number"):
            evaluate_gate(experiment(METRICS, {**METRICS, "avg_model_tokens": -1.0}), {})

    def test_absolute_candidate_failure_is_classified_as_reliability_regression(self):
        candidate = {**METRICS, "infra_failed_rate": 0.1}

        report = evaluate_gate(experiment(candidate, candidate), {})

        self.assertTrue(report["decision"]["correctness_or_reliability_regressed"])

    def test_gate_is_inconclusive_for_missing_metric_instead_of_defaulting_to_zero(self):
        candidate = dict(METRICS)
        candidate.pop("avg_model_tokens")
        report = evaluate_gate(experiment(METRICS, candidate), {})
        self.assertEqual(report["decision"]["status"], "inconclusive")
        self.assertIn("average_model_tokens_limit", report["decision"]["not_available"])

    def test_gate_is_inconclusive_when_a_case_or_trial_is_missing(self):
        incomplete = experiment(METRICS, METRICS)
        incomplete["comparison"]["case_comparisons"] = incomplete["comparison"]["case_comparisons"][:-1]
        report = evaluate_gate(incomplete, {})
        self.assertEqual(report["decision"]["status"], "inconclusive")
        self.assertIn("paired_case_coverage", report["decision"]["hard_blocking_failures"])

        incomplete_trial = experiment(METRICS, METRICS)
        incomplete_trial["comparison"]["case_comparisons"][0]["paired_trial_count"] = 2
        report = evaluate_gate(incomplete_trial, {})
        self.assertEqual(report["decision"]["status"], "inconclusive")
        self.assertIn("paired_trial_coverage", report["decision"]["hard_blocking_failures"])

    def test_gate_requires_platform_origin_for_core_trial_evidence(self):
        document = experiment(METRICS, METRICS)
        document["summaries"] = {
            "baseline": {"jobs": [{"evidence_provenance": {"process_lifecycle": "platform_observed", "test_result": "platform_observed", "git_evidence": "platform_observed"}}]},
            "candidate": {"jobs": [{"evidence_provenance": {"process_lifecycle": "sdk_self_reported", "test_result": "platform_observed", "git_evidence": "platform_observed"}}]},
        }
        report = evaluate_gate(document, {})
        self.assertEqual(report["decision"]["status"], "inconclusive")
        self.assertIn("core_evidence_provenance", report["decision"]["hard_blocking_failures"])
