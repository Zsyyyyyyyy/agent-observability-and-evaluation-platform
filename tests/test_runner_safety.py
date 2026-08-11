import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REGRESSION = Path(__file__).resolve().parents[1]


class RunnerSafetyTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
