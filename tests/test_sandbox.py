import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from regression_lab.sandbox import DockerSandbox, SandboxConfig


class DockerSandboxTests(unittest.TestCase):
    def test_command_has_required_isolation_flags(self):
        with TemporaryDirectory() as directory:
            sandbox = DockerSandbox(directory)
            with patch.object(sandbox, "docker_cli", return_value="/usr/local/bin/docker"):
                argv = sandbox.command("python -m unittest discover -s tests -v")

        self.assertIn("--network", argv)
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop", argv)
        self.assertEqual(argv[argv.index("--cap-drop") + 1], "ALL")
        self.assertIn("--pids-limit", argv)
        self.assertIn("--mount", argv)
        self.assertIn("type=bind", argv[argv.index("--mount") + 1])
        self.assertTrue(argv[argv.index("--mount") + 1].endswith("dst=/workspace"))
        self.assertEqual(argv[-3:], ["sh", "-lc", "python -m unittest discover -s tests -v"])

    def test_config_can_override_limits(self):
        with TemporaryDirectory() as directory:
            sandbox = DockerSandbox(
                directory,
                SandboxConfig(cpus="0.5", memory="256m", pids_limit=32, timeout_seconds=9),
            )
            with patch.object(sandbox, "docker_cli", return_value="docker"):
                argv = sandbox.command("true")

        self.assertEqual(argv[argv.index("--cpus") + 1], "0.5")
        self.assertEqual(argv[argv.index("--memory") + 1], "256m")
        self.assertEqual(argv[argv.index("--pids-limit") + 1], "32")

    def test_run_records_nonzero_command(self):
        with TemporaryDirectory() as directory:
            sandbox = DockerSandbox(directory)
            with patch.object(sandbox, "command", return_value=["sh", "-c", "exit 7"]):
                result = sandbox.run("ignored")

        self.assertEqual(result.status, "command_failed")
        self.assertEqual(result.exit_code, 7)

    def test_timeout_is_reported_as_sandbox_timeout(self):
        with TemporaryDirectory() as directory:
            sandbox = DockerSandbox(directory)
            with patch.object(sandbox, "command", return_value=["sh", "-c", "sleep 10"]):
                with patch.object(sandbox, "_force_remove", return_value=""):
                    with patch(
                        "regression_lab.sandbox.subprocess.run",
                        side_effect=__import__("subprocess").TimeoutExpired(["sh"], 1),
                    ):
                        result = sandbox.run("ignored", timeout_seconds=1)

        self.assertEqual(result.status, "timed_out")
        self.assertEqual(result.exit_code, -1)

    def test_run_assigns_a_container_name_for_cleanup(self):
        with TemporaryDirectory() as directory:
            sandbox = DockerSandbox(directory)
            with patch.object(sandbox, "docker_cli", return_value="docker"):
                with patch("regression_lab.sandbox.subprocess.run") as run:
                    run.return_value.returncode = 0
                    run.return_value.stdout = ""
                    run.return_value.stderr = ""
                    sandbox.run("true")

        argv = run.call_args.args[0]
        self.assertIn("--name", argv)
        self.assertTrue(argv[argv.index("--name") + 1].startswith("regression-lab-"))

    def test_worktree_must_be_a_directory(self):
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            with self.assertRaises(ValueError):
                DockerSandbox(missing)


if __name__ == "__main__":
    unittest.main()
