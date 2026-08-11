import unittest

from src.normalizer import normalize_name


class NormalizerTests(unittest.TestCase):
    def test_none_returns_empty_string(self):
        self.assertEqual(normalize_name(None), "")

    def test_text_is_trimmed_and_lowered(self):
        self.assertEqual(normalize_name("  Alice "), "alice")


if __name__ == "__main__":
    unittest.main()
