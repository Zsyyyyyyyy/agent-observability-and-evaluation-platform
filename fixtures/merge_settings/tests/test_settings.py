import unittest

from src.settings import merge_settings


class SettingsTests(unittest.TestCase):
    def test_none_overrides_use_defaults(self):
        self.assertEqual(merge_settings(None), {"retries": 3, "timeout": 30})

    def test_explicit_override_is_merged(self):
        self.assertEqual(merge_settings({"timeout": 5}), {"retries": 3, "timeout": 5})


if __name__ == "__main__":
    unittest.main()
