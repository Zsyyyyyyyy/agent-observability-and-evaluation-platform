import subprocess
import sys
import unittest
from pathlib import Path

from regression_lab.manifest import expand_trials, load_manifest, validate_manifest


REGRESSION = Path(__file__).resolve().parents[1]
MANIFESTS = (
    "smoke-case-design.yaml",
    "normalize-case-design.yaml",
    "parse-port-case.yaml",
    "safe-slug-case.yaml",
    "bounded-discount-case.yaml",
    "cross-file-greeting-case.yaml",
    "merge-settings-case.yaml",
    "deduplicate-tags-case.yaml",
)


class CoreSuiteTests(unittest.TestCase):
    def test_all_cases_are_valid_and_start_as_repair_tasks(self):
        for filename in MANIFESTS:
            manifest = load_manifest(REGRESSION / "benchmarks" / filename)
            validation = validate_manifest(manifest, REGRESSION)
            self.assertTrue(validation.valid, f"{filename}: {validation.errors}")
            job = expand_trials(manifest, REGRESSION)[0]
            command = str(job["test_command"]).split()
            if command[0] == "python":
                command[0] = sys.executable
            baseline = subprocess.run(
                command,
                cwd=Path(str(job["fixture_path"])),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(baseline.returncode, 0, f"{filename} no longer starts with a failing baseline")


if __name__ == "__main__":
    unittest.main()
