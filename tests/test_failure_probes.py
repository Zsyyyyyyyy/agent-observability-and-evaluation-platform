import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REGRESSION = Path(__file__).resolve().parents[1]


class FailureProbeTests(unittest.TestCase):
    def test_manifest_driven_probes_are_blocked_with_valid_trace(self):
        expectations = {
            "failure-path-violation.yaml": ("completed", "path_policy"),
            "failure-unauthorized-tool.yaml": ("completed", "tool_integrity"),
            "failure-timeout.yaml": ("timed_out", "test"),
        }
        with TemporaryDirectory() as directory:
            for manifest, (status, failed_evaluator) in expectations.items():
                output = Path(directory) / manifest.removesuffix(".yaml")
                completed = subprocess.run(
                    [sys.executable, "scripts/run_benchmark.py", "--manifest", f"benchmarks/{manifest}",
                     "--output-dir", str(output), "--adapter", "failure-probe", "--unsafe-trusted-host"],
                    cwd=REGRESSION, text=True, capture_output=True, check=False,
                )
                result = json.loads(next(output.rglob("result.json")).read_text(encoding="utf-8"))
                scores = {score["evaluator"]: score for score in result["scores"]}
                self.assertEqual(completed.returncode, 1)
                self.assertEqual(result["status"], status)
                self.assertTrue(result["trace_validation"]["valid"])
                self.assertFalse(result["evaluation"]["passed"])
                self.assertFalse(scores[failed_evaluator]["passed"])
