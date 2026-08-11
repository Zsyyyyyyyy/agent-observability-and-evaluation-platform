import unittest

from src.slug import make_slug


class SlugTests(unittest.TestCase):
    def test_punctuation_is_removed(self):
        self.assertEqual(make_slug("Hello, World!"), "hello-world")

    def test_words_are_lowercase_and_joined(self):
        self.assertEqual(make_slug("  Hello   Agent  "), "hello-agent")


if __name__ == "__main__":
    unittest.main()
