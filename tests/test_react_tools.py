import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from adapters.react_agent.tools import ToolExecutor, resolve_tool_policy


class ReactToolTests(unittest.TestCase):
    def test_edit_is_worktree_scoped(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); target = root / "src" / "app.py"; target.parent.mkdir(); target.write_text("value = 1\n", encoding="utf-8")
            tools = ToolExecutor(root, None)
            self.assertEqual(tools.execute("edit_file", {"path": "src/app.py", "old_text": "value = 1", "new_text": "value = 2"}), "edited src/app.py")
            with self.assertRaises(ValueError): tools.execute("read_file", {"path": "../outside"})

    def test_policy_fails_closed(self):
        allowed, denied = resolve_tool_policy({"allowed_tools": ["read_file", "edit_file"], "denied_tools": ["edit_file"]})
        self.assertEqual(allowed, frozenset({"read_file"}))
        self.assertIn("edit_file", denied)

    def test_write_and_edit_enforce_trial_path_policy_before_mutation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "app.py"; source.parent.mkdir(); source.write_text("value = 1\n", encoding="utf-8")
            test = root / "tests" / "test_app.py"; test.parent.mkdir(); test.write_text("assert True\n", encoding="utf-8")
            tools = ToolExecutor(root, None, allowed_paths=("src/**",), forbidden_paths=("tests/**",))

            with self.assertRaisesRegex(PermissionError, "forbidden"):
                tools.execute("write_file", {"path": "tests/test_app.py", "content": "assert False\n"})
            with self.assertRaisesRegex(PermissionError, "not allowed"):
                tools.execute("edit_file", {"path": "README.md", "old_text": "", "new_text": "x"})
            self.assertEqual(test.read_text(encoding="utf-8"), "assert True\n")
            self.assertEqual(tools.execute("edit_file", {"path": "src/app.py", "old_text": "value = 1", "new_text": "value = 2"}), "edited src/app.py")
