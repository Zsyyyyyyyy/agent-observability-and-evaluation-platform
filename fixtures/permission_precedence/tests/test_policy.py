import unittest

from src.policy import is_allowed


class PermissionPrecedenceTests(unittest.TestCase):
    def test_explicitly_allowed_tool_is_permitted(self):
        self.assertTrue(is_allowed("read_file", {"allow": ["read_*"], "deny": []}))

    def test_deny_wins_over_wildcard_allow(self):
        self.assertFalse(is_allowed("delete_file", {"allow": ["*"], "deny": ["delete_*"]}))

    def test_tool_without_matching_allow_is_denied(self):
        self.assertFalse(is_allowed("bash", {"allow": ["read_*"], "deny": []}))


if __name__ == "__main__":
    unittest.main()
