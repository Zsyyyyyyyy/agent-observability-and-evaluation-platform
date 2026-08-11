import sys
import unittest
from pathlib import Path

from regression_lab.runner import run_with_deadline


class RunnerDeadlineTests(unittest.TestCase):
    def test_process_group_is_terminated_at_deadline(self):
        result = run_with_deadline(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            cwd=Path.cwd(),
            env={},
            timeout_seconds=1,
        )

        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
