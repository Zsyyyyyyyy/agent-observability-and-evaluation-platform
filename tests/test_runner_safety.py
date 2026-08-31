import subprocess
import sys
import unittest
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.store import RunStore
from scripts.run_benchmark import _timed_out_result


REGRESSION = Path(__file__).resolve().parents[1]


class RunnerSafetyTests(unittest.TestCase):
    def test_quiet_dry_run_keeps_success_output_empty(self):
        completed = subprocess.run(
            [
                sys.executable, "scripts/run_benchmark.py",
                "--manifest", "benchmarks/smoke-case-design.yaml",
                "--dry-run", "--quiet",
            ],
            cwd=REGRESSION,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")

    def test_parent_timeout_result_is_storeable_and_fails_trace_closed(self):
        with TemporaryDirectory() as directory:
            result = _timed_out_result({"job_id": "case_trial_001"}, 30)
            result["attempt_id"] = "attempt_001"
            store = RunStore(Path(directory) / "runs.db")

            store.record_selected_projection(result, [], "attempt_001")

            persisted = store.get_trial("case_trial_001")
        self.assertEqual(persisted["status"], "timed_out")
        self.assertTrue(persisted["trace_id"].startswith("trace_parent_timeout_"))
        self.assertFalse(persisted["trace_validation"]["valid"])

    def test_runner_refuses_unowned_existing_job_directory(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "smoke_calculator_empty_input_trial_001").mkdir()
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/run_benchmark.py",
                    "--manifest", "benchmarks/smoke-case-design.yaml",
                    "--output-dir", str(output),
                    "--unsafe-trusted-host",
                ],
                cwd=REGRESSION,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("REFUSING UNOWNED OUTPUT DIRECTORY", completed.stderr)

    def test_resume_rerun_invalid_preserves_old_attempt_and_selects_new_attempt(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            command = [
                sys.executable, "scripts/run_benchmark.py", "--manifest", "benchmarks/failure-timeout.yaml",
                "--output-dir", str(output), "--unsafe-trusted-host", "--adapter", "failure-probe",
            ]
            first = subprocess.run(command, cwd=REGRESSION, capture_output=True, text=True, check=False)
            retry = subprocess.run([*command, "--resume", "--rerun-invalid"], cwd=REGRESSION, capture_output=True, text=True, check=False)
            job_dir = next(path for path in output.iterdir() if path.is_dir() and path.name != "invalid-attempts")
            attempts = sorted((job_dir / "attempts").iterdir())
            selected = json.loads((job_dir / "selected-attempt.json").read_text(encoding="utf-8"))
            self.assertEqual(first.returncode, 1)
            self.assertEqual(retry.returncode, 1)
            self.assertEqual([path.name for path in attempts], ["attempt_001", "attempt_002"])
            self.assertTrue((attempts[0] / "result.json").is_file())
            self.assertTrue((attempts[1] / "result.json").is_file())
            self.assertEqual(selected["attempt_id"], "attempt_002")
            with sqlite3.connect(output / "runs.db") as connection:
                stored = json.loads(connection.execute("SELECT result_json FROM trials WHERE trial_id = ?", ("failure_timeout_trial_001",)).fetchone()[0])
            self.assertEqual(stored["attempt_id"], selected["attempt_id"])

    def test_resume_rerun_completed_preserves_valid_attempt_and_selects_new_attempt(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            command = [
                sys.executable, "scripts/run_benchmark.py", "--manifest", "benchmarks/smoke-case-design.yaml",
                "--output-dir", str(output), "--unsafe-trusted-host", "--adapter", "react-agent",
            ]
            first = subprocess.run(command, cwd=REGRESSION, capture_output=True, text=True, check=False)
            retry = subprocess.run([*command, "--resume", "--rerun-completed"], cwd=REGRESSION, capture_output=True, text=True, check=False)
            job_dir = next(path for path in output.iterdir() if path.is_dir() and path.name != "invalid-attempts")
            attempts = sorted((job_dir / "attempts").iterdir())
            selected = json.loads((job_dir / "selected-attempt.json").read_text(encoding="utf-8"))
            self.assertIn(first.returncode, {0, 1}, first.stderr)
            self.assertIn(retry.returncode, {0, 1}, retry.stderr)
            self.assertEqual([path.name for path in attempts], ["attempt_001", "attempt_002"])
            self.assertEqual(selected["attempt_id"], "attempt_002")


if __name__ == "__main__":
    unittest.main()
