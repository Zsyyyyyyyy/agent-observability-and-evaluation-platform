import unittest

from src.resolver import resolve_settings


PROFILES = {
    "base": {"settings": {"region": "cn", "retries": 2, "audit": True}},
    "staging": {"extends": "base", "settings": {"retries": 3}},
    "canary": {"extends": "staging", "settings": {"audit": False}},
}


class ConfigInheritanceTests(unittest.TestCase):
    def test_child_inherits_unspecified_parent_settings(self):
        self.assertEqual(
            resolve_settings(PROFILES, "staging"),
            {"region": "cn", "retries": 3, "audit": True},
        )

    def test_nested_inheritance_keeps_child_precedence(self):
        self.assertEqual(
            resolve_settings(PROFILES, "canary"),
            {"region": "cn", "retries": 3, "audit": False},
        )

    def test_unknown_parent_is_not_silently_ignored(self):
        with self.assertRaises(KeyError):
            resolve_settings({"child": {"extends": "missing", "settings": {}}}, "child")


if __name__ == "__main__":
    unittest.main()
