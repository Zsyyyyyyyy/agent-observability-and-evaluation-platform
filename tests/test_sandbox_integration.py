import os
import unittest
from tempfile import TemporaryDirectory

from regression_lab.sandbox import DockerSandbox, SandboxConfig, SandboxUnavailable


@unittest.skipUnless(
    os.environ.get("RUN_DOCKER_INTEGRATION") == "1",
    "set RUN_DOCKER_INTEGRATION=1 to run Docker integration tests",
)
class DockerSandboxIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        available, detail = DockerSandbox.available()
        if not available:
            raise unittest.SkipTest(f"Docker unavailable: {detail}")
        cls.worktree = TemporaryDirectory()
        cls.sandbox = DockerSandbox(
            cls.worktree.name,
            SandboxConfig(timeout_seconds=2),
        )

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "worktree"):
            cls.worktree.cleanup()

    def test_network_none_blocks_tcp_connection(self):
        result = self.sandbox.run(
            "python -c \"import socket; socket.create_connection(('example.com', 80), 1)\""
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn(result.status, {"command_failed", "timed_out"})

    def test_read_only_root_and_tmpfs_policy(self):
        result = self.sandbox.run(
            "python -c \"from pathlib import Path; "
            "Path('/tmp/sandbox-ok').write_text('ok'); print('tmp-ok'); "
            "Path('/etc/regression-lab-should-fail').write_text('blocked')\""
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("tmp-ok", result.stdout)

    def test_command_timeout_is_enforced_by_runner(self):
        result = self.sandbox.run("python -c 'import time; time.sleep(5)'")

        self.assertEqual(result.status, "timed_out")
        self.assertEqual(result.exit_code, -1)


if __name__ == "__main__":
    unittest.main()
