import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import unittest

from calculator import calculate


class CalculatorTests(unittest.TestCase):
    def test_empty_input_returns_zero(self):
        self.assertEqual(calculate(""), 0)

    def test_number_keeps_existing_behavior(self):
        self.assertEqual(calculate("4"), 5)


if __name__ == "__main__":
    unittest.main()

