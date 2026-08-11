import unittest

from regression_lab.experiment import compare_summaries, expand_experiment


class ExperimentTests(unittest.TestCase):
    def test_expands_case_trials_for_each_agent(self):
        jobs = [{"job_id": "case_trial_001", "case_id": "case", "trial_index": 1}]
        expanded = expand_experiment(jobs, [
            {"id": "baseline", "version": "v1"},
            {"id": "candidate", "version": "v2"},
        ])

        self.assertEqual(len(expanded), 2)
        self.assertEqual(expanded[1]["experiment_job_id"], "candidate__case_trial_001")

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


if __name__ == "__main__":
    unittest.main()
