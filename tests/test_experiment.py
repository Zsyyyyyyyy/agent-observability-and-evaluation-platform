import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.experiment import compare_summaries, expand_experiment
from scripts.run_experiment import _hydrate_trial_diagnostics, build_comparison_arms, pairwise_report


class ExperimentTests(unittest.TestCase):
    def test_expands_case_trials_for_each_agent(self):
        jobs = [{"job_id": "case_trial_001", "case_id": "case", "trial_index": 1}]
        expanded = expand_experiment(jobs, [
            {"id": "baseline", "version": "v1"},
            {"id": "candidate", "version": "v2"},
        ])

        self.assertEqual(len(expanded), 2)
        self.assertEqual(expanded[1]["experiment_job_id"], "candidate__case_trial_001")

    def test_multi_arm_report_compares_each_candidate_to_the_champion(self):
        summaries = {
            "champion": {"jobs": [{"status": "completed", "evaluation_passed": True, "duration_ms": 100}]},
            "positive": {"jobs": [{"status": "completed", "evaluation_passed": True, "duration_ms": 80}]},
            "negative": {"jobs": [{"status": "completed", "evaluation_passed": True, "duration_ms": 120}]},
        }
        agents = [{"id": "champion", "version": "v3"}, {"id": "positive", "version": "v4.1"}, {"id": "negative", "version": "v4-negative"}]
        arms = build_comparison_arms(agents, summaries)
        self.assertEqual(set(arms), {"positive", "negative"})
        self.assertEqual(arms["positive"]["comparison"]["delta"]["avg_duration_ms"], -20.0)
        self.assertEqual(arms["negative"]["comparison"]["delta"]["avg_duration_ms"], 20.0)
        report = {"agents": agents, "summaries": summaries, "metrics_version": 3, "trial_count_required_per_case": 3, "protocol": {}}
        self.assertEqual([item["id"] for item in pairwise_report(report, arms["positive"])["agents"]], ["champion", "positive"])

    def test_comparison_classifies_candidate_improvement(self):
        baseline = {"jobs": [{"status": "completed", "evaluation_passed": False, "test_passed": False,
                              "tool_calls": 4, "duration_ms": 100, "added_lines": 20, "deleted_lines": 2}]}
        candidate = {"jobs": [{"status": "completed", "evaluation_passed": True, "test_passed": True,
                                "tool_calls": 3, "duration_ms": 80, "added_lines": 4, "deleted_lines": 1}]}

        comparison = compare_summaries(baseline, candidate)

        self.assertEqual(comparison["delta"]["test_pass_rate"], 1.0)
        self.assertIn("test_pass_rate", comparison["classification"]["improved"])
        self.assertEqual(comparison["delta"]["avg_tool_calls"], -1)
        self.assertIn("avg_tool_calls", comparison["classification"]["improved"])

    def test_comparison_treats_model_failures_as_reliability_regression(self):
        baseline = {"jobs": [{"status": "completed", "evaluation_passed": True, "test_passed": True}]}
        candidate = {"jobs": [{"status": "model_failed", "evaluation_passed": False, "test_passed": False}]}

        comparison = compare_summaries(baseline, candidate)

        self.assertEqual(comparison["delta"]["model_failed_rate"], 1.0)
        self.assertIn("model_failed_rate", comparison["classification"]["regressed"])

    def test_empty_diff_from_external_failure_is_not_a_diff_policy_violation(self):
        baseline = {"jobs": [{"status": "completed", "evaluation_passed": True, "diff_policy_violated": False}]}
        candidate = {"jobs": [{"status": "model_failed", "evaluation_passed": False, "diff_policy_violated": False}]}
        comparison = compare_summaries(baseline, candidate)
        self.assertEqual(comparison["candidate"]["diff_policy_violation_rate"], 0.0)

    def test_comparison_reports_pass_at_three_flakiness_percentiles_and_pairs(self):
        baseline = {"jobs": [
            {"job_id": "a1", "case_id": "case_a", "trial_index": 1, "status": "completed", "evaluation_passed": True, "trace_valid": True, "duration_ms": 10, "model_tokens": 100, "tool_calls": 1},
            {"job_id": "a2", "case_id": "case_a", "trial_index": 2, "status": "completed", "evaluation_passed": True, "trace_valid": True, "duration_ms": 20, "model_tokens": 200, "tool_calls": 2},
            {"job_id": "a3", "case_id": "case_a", "trial_index": 3, "status": "completed", "evaluation_passed": True, "trace_valid": True, "duration_ms": 100, "model_tokens": 300, "tool_calls": 3},
            {"job_id": "b1", "case_id": "case_b", "trial_index": 1, "status": "completed", "evaluation_passed": True, "trace_valid": True},
            {"job_id": "b2", "case_id": "case_b", "trial_index": 2, "status": "agent_failed", "evaluation_passed": False, "trace_valid": True},
            {"job_id": "b3", "case_id": "case_b", "trial_index": 3, "status": "agent_failed", "evaluation_passed": False, "trace_valid": True},
        ]}
        candidate = {"jobs": [
            {**job, "duration_ms": 5, "model_tokens": 50, "tool_calls": 1}
            for job in baseline["jobs"]
        ]}

        comparison = compare_summaries(baseline, candidate)

        reliability = comparison["reliability"]["baseline"]
        self.assertEqual(reliability["eligible_case_count"], 2)
        self.assertEqual(reliability["all_pass_at_k"], 0.5)
        self.assertEqual(reliability["flaky_case_rate"], 0.5)
        self.assertEqual(comparison["baseline"]["p50_duration_ms"], 20.0)
        self.assertEqual(comparison["baseline"]["p95_duration_ms"], 100.0)
        case_a = next(item for item in comparison["case_comparisons"] if item["case_id"] == "case_a")
        self.assertEqual(case_a["paired_trial_count"], 3)
        self.assertEqual(case_a["paired_trials"][0]["delta"]["duration_ms"], -5)
        self.assertEqual(comparison["behavior"]["baseline"]["instrumented_trial_count"], 0)
        self.assertIn("all_behavior_metrics", comparison["behavior"]["baseline"]["unavailable"])

    def test_reliability_marks_insufficient_case_data_not_available(self):
        comparison = compare_summaries(
            {"jobs": [{"case_id": "case", "trial_index": 1, "status": "completed", "evaluation_passed": True}]},
            {"jobs": [{"case_id": "case", "trial_index": 1, "status": "completed", "evaluation_passed": True}]},
        )

        reliability = comparison["reliability"]["baseline"]
        self.assertIsNone(reliability["all_pass_at_k"])
        self.assertEqual(reliability["unavailable_cases"][0]["reason"], "insufficient_trials")

    def test_clustered_case_bootstrap_reports_paired_interval_and_case_outcomes(self):
        baseline = {"jobs": []}
        candidate = {"jobs": []}
        for case_id, before, after in (("case_a", 100, 80), ("case_b", 200, 170), ("case_c", 300, 280)):
            for trial_index in range(1, 4):
                shared = {"case_id": case_id, "trial_index": trial_index, "status": "completed", "evaluation_passed": True, "trace_valid": True, "model_tokens": 1000, "tool_calls": 4}
                baseline["jobs"].append({**shared, "duration_ms": before + trial_index})
                candidate["jobs"].append({**shared, "duration_ms": after + trial_index, "model_tokens": 1010, "tool_calls": 5})

        statistics = compare_summaries(baseline, candidate)["statistics"]
        latency = statistics["metrics"]["duration_ms"]

        self.assertEqual(statistics["method"], "clustered_case_bootstrap")
        self.assertEqual(statistics["eligible_case_count"], 3)
        self.assertEqual(latency["paired_trial_count"], 9)
        self.assertLess(latency["ci95"]["high"], 0)
        self.assertEqual(latency["case_outcomes"], {"candidate_lower": 3, "candidate_higher": 0, "tied": 0})
        self.assertEqual(statistics["conclusion"]["level"], "limited_coverage")

    def test_clustered_case_bootstrap_excludes_failed_pairs(self):
        baseline = {"jobs": [{"case_id": "case", "trial_index": 1, "status": "completed", "evaluation_passed": True, "trace_valid": True, "duration_ms": 100}]}
        candidate = {"jobs": [{"case_id": "case", "trial_index": 1, "status": "model_failed", "evaluation_passed": False, "trace_valid": True, "duration_ms": 10}]}
        statistics = compare_summaries(baseline, candidate, required_trials_per_case=1)["statistics"]
        self.assertEqual(statistics["excluded_paired_trial_count"], 1)
        self.assertEqual(statistics["conclusion"]["level"], "not_available")

    def test_report_only_hydrates_behavior_from_preserved_trial_result(self):
        with TemporaryDirectory() as directory:
            case_dir = Path(directory)
            trial_dir = case_dir / "case_trial_001"
            trial_dir.mkdir()
            trace = trial_dir / "trace.jsonl"
            trace.write_text(
                json.dumps({"kind": "span_start", "event_seq": 1, "span_id": "tool", "name": "tool.call", "attributes": {"tool_name": "glob"}})
                + "\n" + json.dumps({"kind": "span_end", "event_seq": 2, "span_id": "tool", "status": "ok", "attributes": {}}),
                encoding="utf-8",
            )
            (trial_dir / "result.json").write_text(json.dumps({"trace_path": str(trace)}), encoding="utf-8")
            hydrated = _hydrate_trial_diagnostics({"jobs": [{"job_id": "case_trial_001"}]}, case_dir)
        self.assertEqual(hydrated["jobs"][0]["behavior"]["tool_success_rate"], 1.0)

    def test_report_only_hydrates_selected_attempt_source_identity(self):
        with TemporaryDirectory() as directory:
            case_dir = Path(directory)
            trial_dir = case_dir / "case_trial_001"
            trial_dir.mkdir()
            (trial_dir / "result.json").write_text(
                json.dumps({
                    "agent_source_hash": "sha256:actual",
                    "expected_agent_source_hash": "sha256:frozen",
                    "agent_source_hash_matches_protocol": False,
                }),
                encoding="utf-8",
            )
            hydrated = _hydrate_trial_diagnostics(
                {"jobs": [{"job_id": "case_trial_001", "agent_source_hash": "sha256:stale"}]}, case_dir,
            )
        self.assertEqual(hydrated["jobs"][0]["agent_source_hash"], "sha256:actual")
        self.assertFalse(hydrated["jobs"][0]["agent_source_hash_matches_protocol"])


if __name__ == "__main__":
    unittest.main()
