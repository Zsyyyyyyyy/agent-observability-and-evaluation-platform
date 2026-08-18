import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_failure_suite.py"
SPEC = importlib.util.spec_from_file_location("run_failure_suite", SCRIPT)
assert SPEC and SPEC.loader
failure_suite = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(failure_suite)


class FailureSuiteTests(unittest.TestCase):
    def test_accepts_a_fresh_expected_failure_or_a_resumed_validated_artifact(self):
        result = {
            "status": "completed",
            "trace_validation": {"valid": True},
            "scores": [{"evaluator": "path_policy", "passed": False}],
        }

        self.assertTrue(failure_suite.probe_passed(1, result, "completed", "path_policy"))
        self.assertTrue(failure_suite.probe_passed(0, result, "completed", "path_policy"))

    def test_rejects_a_resumed_artifact_without_the_expected_evidence(self):
        result = {
            "status": "completed",
            "trace_validation": {"valid": True},
            "scores": [{"evaluator": "path_policy", "passed": True}],
        }

        self.assertFalse(failure_suite.probe_passed(0, result, "completed", "path_policy"))
        self.assertFalse(failure_suite.probe_passed(2, result, "completed", "path_policy"))

    def test_relocated_case_dir_is_namespaced_and_deterministic(self):
        root = Path("/tmp/failure-suite")

        first = failure_suite.relocated_case_dir(root, "failure-path-violation.yaml")
        second = failure_suite.relocated_case_dir(root, "failure-path-violation.yaml")

        self.assertEqual(first, second)
        self.assertEqual(first.name, "failure-path-violation")
        self.assertTrue(first.parent.name.startswith("workspace-"))
        self.assertNotEqual(first, failure_suite.relocated_case_dir(root, "failure-path-violation.yaml", retry=1))
