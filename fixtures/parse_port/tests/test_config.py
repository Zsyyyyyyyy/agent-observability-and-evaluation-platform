import unittest

from src.config import parse_port


class ParsePortTests(unittest.TestCase):
    def test_blank_value_uses_default_port(self):
        self.assertEqual(parse_port(""), 8000)

    def test_valid_port_is_unchanged(self):
        self.assertEqual(parse_port("8080"), 8080)


if __name__ == "__main__":
    unittest.main()
