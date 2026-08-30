import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.git_sources import GitSourceError, create_git_source_snapshots, inspect_git_sources, module_exists


class GitSourceTests(unittest.TestCase):
    def _git(self, root: Path, *arguments: str) -> str:
        completed = subprocess.run(["git", *arguments], cwd=root, text=True, capture_output=True, check=True)
        return completed.stdout

    def _repository(self) -> tuple[TemporaryDirectory[str], Path]:
        directory = TemporaryDirectory()
        root = Path(directory.name)
        self._git(root, "init")
        self._git(root, "config", "user.email", "test@example.invalid")
        self._git(root, "config", "user.name", "Regression Lab Test")
        (root / "agent.py").write_text("VERSION = 'baseline'\n", encoding="utf-8")
        self._git(root, "add", ".")
        self._git(root, "commit", "-m", "baseline")
        return directory, root

    def test_working_tree_snapshot_preserves_original_and_excludes_local_secrets(self):
        directory, repository = self._repository()
        with directory, TemporaryDirectory() as outside_directory:
            before_head = self._git(repository, "rev-parse", "HEAD").strip()
            (repository / "agent.py").write_text("VERSION = 'candidate'\n", encoding="utf-8")
            (repository / "extra.py").write_text("EXTRA = True\n", encoding="utf-8")
            (repository / ".env").write_text("SECRET=value\n", encoding="utf-8")
            (repository / ".venv").mkdir()
            (repository / ".venv" / "token").write_text("SECRET=value\n", encoding="utf-8")
            (repository / "local.pem").write_text("private-key\n", encoding="utf-8")
            (repository / "secrets.json").write_text('{"token":"value"}\n', encoding="utf-8")
            (repository / ".aws").mkdir()
            (repository / ".aws" / "credentials").write_text("[default]\n", encoding="utf-8")
            outside_secret = Path(outside_directory) / "outside-secret.txt"
            outside_secret.write_text("SECRET=value\n", encoding="utf-8")
            (repository / "outside-secret-link").symlink_to(outside_secret)
            before_status = self._git(repository, "status", "--porcelain")

            plan = inspect_git_sources(repository, "HEAD", "working_tree")
            snapshots = create_git_source_snapshots(plan)
            snapshot_root = Path(snapshots.directory.name)
            try:
                self.assertEqual(plan.candidate_revision, before_head)
                self.assertTrue(plan.candidate_dirty)
                self.assertEqual((snapshots.baseline_root / "agent.py").read_text(encoding="utf-8"), "VERSION = 'baseline'\n")
                self.assertEqual((snapshots.candidate_root / "agent.py").read_text(encoding="utf-8"), "VERSION = 'candidate'\n")
                self.assertTrue((snapshots.candidate_root / "extra.py").is_file())
                self.assertFalse((snapshots.candidate_root / ".env").exists())
                self.assertFalse((snapshots.candidate_root / ".venv").exists())
                self.assertFalse((snapshots.candidate_root / "local.pem").exists())
                self.assertFalse((snapshots.candidate_root / "secrets.json").exists())
                self.assertFalse((snapshots.candidate_root / ".aws").exists())
                self.assertFalse((snapshots.candidate_root / "outside-secret-link").exists())
                self.assertEqual(self._git(repository, "rev-parse", "HEAD").strip(), before_head)
                self.assertEqual(self._git(repository, "status", "--porcelain"), before_status)
            finally:
                snapshots.cleanup()
            self.assertFalse(snapshot_root.exists())

    def test_invalid_ref_and_non_root_are_rejected(self):
        directory, repository = self._repository()
        with directory:
            with self.assertRaisesRegex(GitSourceError, "Baseline ref"):
                inspect_git_sources(repository, "missing-ref", "working_tree")
            child = repository / "child"; child.mkdir()
            with self.assertRaisesRegex(GitSourceError, "repository root"):
                inspect_git_sources(child, "HEAD", "working_tree")

    def test_working_tree_snapshot_applies_staged_unstaged_deleted_and_mode_changes(self):
        directory, repository = self._repository()
        with directory:
            (repository / "remove.py").write_text("REMOVE = True\n", encoding="utf-8")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "tracked file")
            (repository / "agent.py").write_text("VERSION = 'candidate'\n", encoding="utf-8")
            (repository / "agent.py").chmod(0o755)
            (repository / "remove.py").unlink()
            (repository / "staged.py").write_text("STAGED = True\n", encoding="utf-8")
            self._git(repository, "add", "staged.py")

            snapshots = create_git_source_snapshots(inspect_git_sources(repository, "HEAD", "working_tree"))
            try:
                self.assertEqual((snapshots.candidate_root / "agent.py").read_text(encoding="utf-8"), "VERSION = 'candidate'\n")
                self.assertTrue((snapshots.candidate_root / "agent.py").stat().st_mode & 0o100)
                self.assertFalse((snapshots.candidate_root / "remove.py").exists())
                self.assertTrue((snapshots.candidate_root / "staged.py").is_file())
            finally:
                snapshots.cleanup()

    def test_module_entry_exists_in_the_selected_revision(self):
        directory, repository = self._repository()
        with directory:
            (repository / "package").mkdir()
            (repository / "package" / "__init__.py").write_text("", encoding="utf-8")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "package without entry")
            plan = inspect_git_sources(repository, "HEAD", "git_ref", "HEAD")
            self.assertFalse(module_exists(plan, "package", candidate=False))
            (repository / "package" / "__main__.py").write_text("", encoding="utf-8")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "add module")

            plan = inspect_git_sources(repository, "HEAD", "git_ref", "HEAD")
            self.assertTrue(module_exists(plan, "package", candidate=False))
            self.assertFalse(module_exists(plan, "missing", candidate=True))

    def test_commit_to_commit_snapshots_use_distinct_revisions(self):
        directory, repository = self._repository()
        with directory:
            baseline = self._git(repository, "rev-parse", "HEAD").strip()
            (repository / "agent.py").write_text("VERSION = 'candidate'\n", encoding="utf-8")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "candidate")
            candidate = self._git(repository, "rev-parse", "HEAD").strip()

            snapshots = create_git_source_snapshots(inspect_git_sources(repository, baseline, "git_ref", candidate))
            try:
                self.assertNotEqual(snapshots.plan.baseline_revision, snapshots.plan.candidate_revision)
                self.assertEqual((snapshots.baseline_root / "agent.py").read_text(encoding="utf-8"), "VERSION = 'baseline'\n")
                self.assertEqual((snapshots.candidate_root / "agent.py").read_text(encoding="utf-8"), "VERSION = 'candidate'\n")
            finally:
                snapshots.cleanup()
