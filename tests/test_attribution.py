import unittest

from regression_lab.attribution import aggregate_attribution, attribute_trial


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
        self.assertEqual(attributed, {"kind": "agent", "reason": "task_test_failed_or_not_run"})

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
