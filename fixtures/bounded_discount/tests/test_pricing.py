import unittest

from src.pricing import apply_discount


class PricingTests(unittest.TestCase):
    def test_discount_is_capped_at_full_price(self):
        self.assertEqual(apply_discount(100, 150), 0.0)

    def test_normal_discount_still_works(self):
        self.assertEqual(apply_discount(100, 20), 80.0)


if __name__ == "__main__":
    unittest.main()
