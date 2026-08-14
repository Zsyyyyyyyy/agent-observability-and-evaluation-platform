import unittest

from src.loader import load_profile


class ProfileMigrationTests(unittest.TestCase):
    def test_current_snapshot_keeps_its_fields(self):
        self.assertEqual(
            load_profile({"version": 2, "id": "u-1", "display_name": "Ada", "settings": {"theme": "dark"}}),
            {"version": 2, "id": "u-1", "display_name": "Ada", "settings": {"theme": "dark"}},
        )

    def test_v1_snapshot_migrates_legacy_name(self):
        self.assertEqual(
            load_profile({"version": 1, "id": "u-1", "name": "Ada", "settings": {"theme": "dark"}}),
            {"version": 2, "id": "u-1", "display_name": "Ada", "settings": {"theme": "dark"}},
        )

    def test_v1_snapshot_without_settings_gets_a_new_empty_mapping(self):
        first = load_profile({"version": 1, "id": "u-1", "name": "Ada"})
        second = load_profile({"version": 1, "id": "u-2", "name": "Lin"})
        first["settings"]["theme"] = "dark"
        self.assertEqual(second["settings"], {})


if __name__ == "__main__":
    unittest.main()
