import unittest

from src.cache import read_cached


class CacheExpiryTests(unittest.TestCase):
    def test_fresh_entry_is_returned(self):
        self.assertEqual(read_cached({"user": {"value": "Ada", "expires_at": 20}}, "user", now=19), "Ada")

    def test_entry_at_expiry_boundary_is_a_miss(self):
        self.assertIsNone(read_cached({"user": {"value": "Ada", "expires_at": 20}}, "user", now=20))

    def test_missing_key_is_a_miss(self):
        self.assertIsNone(read_cached({}, "user", now=0))


if __name__ == "__main__":
    unittest.main()
