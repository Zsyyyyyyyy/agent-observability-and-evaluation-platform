import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from regression_lab.manifest import ManifestError, expand_trials, load_manifest, safe_child_path, validate_manifest


REGRESSION = Path(__file__).resolve().parents[1]


class ManifestTests(unittest.TestCase):
    def test_smoke_manifest_loads_without_yaml_dependency(self):
        manifest = load_manifest(REGRESSION / "benchmarks" / "smoke-case-design.yaml")
        validation = validate_manifest(manifest, REGRESSION)

        self.assertTrue(validation.valid, validation.errors)
        jobs = expand_trials(manifest, REGRESSION, trials_override=2)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["allowed_paths"], ["src/**"])
        self.assertEqual(jobs[0]["budget"]["max_tool_calls"], 24)

    def test_invalid_manifest_reports_nested_fields(self):
        validation = validate_manifest({"schema_version": 1}, REGRESSION)

        self.assertFalse(validation.valid)
        self.assertTrue(any(error == "id is required" for error in validation.errors))
        self.assertTrue(any(error == "fixture must be a map" for error in validation.errors))

    def test_manifest_rejects_identifier_and_fixture_path_escape(self):
        manifest = load_manifest(REGRESSION / "benchmarks" / "smoke-case-design.yaml")
        manifest["id"] = "../../outside"
        manifest["fixture"]["path"] = "../outside"

        validation = validate_manifest(manifest, REGRESSION)

        self.assertFalse(validation.valid)
        self.assertTrue(any("id must match" in error for error in validation.errors))
        self.assertTrue(any("fixture.path escapes project_root" in error for error in validation.errors))

    def test_safe_child_rejects_symlink_escape(self):
        with TemporaryDirectory() as directory, TemporaryDirectory() as outside:
            root = Path(directory)
            (root / "safe-id").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ManifestError):
                safe_child_path(root, "safe-id", "job_id")


if __name__ == "__main__":
    unittest.main()
