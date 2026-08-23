import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.evaluators import (
    BudgetEvaluator,
    DiffEvaluator,
    PathPolicyEvaluator,
    TestEvaluator,
    ToolIntegrityEvaluator,
    evaluate_baseline,
)


class EvaluatorTests(unittest.TestCase):
    def test_baseline_passes_for_valid_trial(self):
        result = {
            "trial_id": "trial_1",
            "trace_id": "trace_1",
            "status": "completed",
            "test_exit_code": 0,
            "test_stdout": "Ran 2 tests in 0.01s\n\nOK",
            "test_stderr": "",
            "changed_files": ["src/calculator.py"],
            "allowed_paths": ["src/**"],
            "forbidden_paths": ["tests/**"],
            "trace_validation": {"valid": True, "errors": []},
            "git_diff": "--- a/src/calculator.py\n+++ b/src/calculator.py\n+return 0\n",
            "git_evidence": {"diff_base": "HEAD", "captures_untracked": True},
        }

        evaluation = evaluate_baseline(result)

        self.assertTrue(evaluation["passed"])
        self.assertEqual(len(evaluation["scores"]), 6)
        self.assertEqual(evaluation["scores"][0]["actual"]["run"], 2)

    def test_test_and_path_failures_include_evidence(self):
        result = {
            "test_exit_code": 1,
            "test_stdout": "Ran 2 tests in 0.01s\nFAILED (failures=1)",
            "test_stderr": "assertion failed",
            "changed_files": ["tests/test_calculator.py", "README.md"],
        }

        test_score = TestEvaluator().evaluate(result)
        path_score = PathPolicyEvaluator(["src/**"], ["tests/**"]).evaluate(result)

        self.assertFalse(test_score.passed)
        self.assertEqual(test_score.actual["failures"], 1)
        self.assertIn("assertion failed", test_score.evidence["stderr"])
        self.assertFalse(path_score.passed)
        self.assertEqual(set(path_score.evidence["violating_files"]), {"README.md", "tests/test_calculator.py"})

    def test_zero_test_command_is_not_a_pass(self):
        score = TestEvaluator().evaluate({
            "test_exit_code": 0,
            "test_stdout": "Ran 0 tests in 0.000s\n\nOK",
            "test_stderr": "",
        })

        self.assertFalse(score.passed)
        self.assertEqual(score.actual["run"], 0)

    def test_manifest_selected_evaluators_and_acceptance_are_applied(self):
        result = {
            "status": "completed", "test_exit_code": 0,
            "test_stdout": "Ran 1 test in 0.01s\nOK", "test_stderr": "",
            "changed_files": ["src/a.py"], "allowed_paths": ["src/**"], "forbidden_paths": ["tests/**"],
        }

        evaluation = evaluate_baseline(
            result, required=["test", "path_policy"],
            acceptance=["test_exit_code == 0", "forbidden_path_changes == 0", "result_status == completed"],
        )

        self.assertTrue(evaluation["passed"])
        self.assertEqual(evaluation["required_evaluators"], ["test", "path_policy"])
        self.assertEqual(len(evaluation["scores"]), 6)
        self.assertTrue(evaluation["acceptance"]["passed"])

    def test_diff_budget_and_tool_integrity_fail_with_specific_evidence(self):
        with TemporaryDirectory() as directory:
            trace_path = Path(directory) / "trace.jsonl"
            trace_path.write_text(
                "\n".join([
                    '{"trace_id":"t","event_seq":1,"ts":1,"kind":"span_start","span_id":"root","name":"agent.run","attributes":{}}',
                    '{"trace_id":"t","event_seq":2,"ts":2,"kind":"span_start","span_id":"tool","name":"tool.call","attributes":{"tool_name":"connect_mcp"}}',
                    '{"trace_id":"t","event_seq":3,"ts":3,"kind":"span_end","span_id":"tool","status":"ok","attributes":{}}',
                    '{"trace_id":"t","event_seq":4,"ts":4,"kind":"span_end","span_id":"root","status":"ok","attributes":{"duration_ms":99}}',
                ]) + "\n",
                encoding="utf-8",
            )
            result = {
                "changed_files": ["src/a.py"],
                "git_diff": "--- a/src/a.py\n+++ b/src/a.py\n" + "+x\n" * 4,
                "trace_path": str(trace_path),
                "allowed_tools": ["read_file"],
                "budget": {"max_tool_calls": 0, "max_duration_ms": 10},
            }

            diff = DiffEvaluator(max_added_lines=2).evaluate(result)
            integrity = ToolIntegrityEvaluator(["read_file"]).evaluate(result)
            budget = BudgetEvaluator().evaluate(result)

        self.assertFalse(diff.passed)
        self.assertIn("too_many_added_lines", diff.actual["violations"])
        self.assertFalse(integrity.passed)
        self.assertEqual(integrity.actual["unauthorized"], ["connect_mcp"])
        self.assertFalse(budget.passed)
        self.assertEqual(budget.actual["tool_calls"], 1)

    def test_malformed_trace_attributes_do_not_crash_evaluation(self):
        with TemporaryDirectory() as directory:
            trace_path = Path(directory) / "trace.jsonl"
            trace_path.write_text(
                '\n'.join([
                    '{"kind":"span_start","span_id":"tool","name":"tool.call","attributes":["invalid"]}',
                    '{"kind":"span_end","span_id":"tool","status":"ok","attributes":"invalid"}',
                ]),
                encoding="utf-8",
            )
            result = {
                "trace_path": str(trace_path),
                "trace_validation": {"valid": False, "errors": ["malformed attributes"]},
                "allowed_tools": ["read_file"],
            }

            evaluation = evaluate_baseline(result)

        self.assertFalse(evaluation["passed"])
        self.assertEqual(len(evaluation["scores"]), 6)


if __name__ == "__main__":
    unittest.main()
