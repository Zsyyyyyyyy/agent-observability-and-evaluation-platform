import unittest

from src.tags import normalize_tags


class TagTests(unittest.TestCase):
    def test_duplicate_tags_are_removed_in_first_seen_order(self):
        self.assertEqual(normalize_tags(["Python", " python ", "AI"]), ["python", "ai"])

    def test_distinct_tags_are_normalized(self):
        self.assertEqual(normalize_tags([" Code ", "Agent"]), ["code", "agent"])


if __name__ == "__main__":
    unittest.main()
