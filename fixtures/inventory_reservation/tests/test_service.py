import unittest

from src.service import reserve_all


class InventoryReservationTests(unittest.TestCase):
    def test_reserves_every_valid_line_in_request_order(self):
        stock = {"apple": 5, "pear": 2}
        reserved = reserve_all(stock, [{"sku": "apple", "quantity": 2}, {"sku": "pear", "quantity": 1}])
        self.assertEqual(reserved, [{"sku": "apple", "quantity": 2}, {"sku": "pear", "quantity": 1}])
        self.assertEqual(stock, {"apple": 3, "pear": 1})

    def test_late_insufficient_line_leaves_stock_unchanged(self):
        stock = {"apple": 5, "pear": 1}
        with self.assertRaisesRegex(ValueError, "insufficient stock for pear"):
            reserve_all(stock, [{"sku": "apple", "quantity": 2}, {"sku": "pear", "quantity": 2}])
        self.assertEqual(stock, {"apple": 5, "pear": 1})

    def test_repeated_sku_is_checked_as_one_atomic_batch(self):
        stock = {"apple": 3}
        with self.assertRaisesRegex(ValueError, "insufficient stock for apple"):
            reserve_all(stock, [{"sku": "apple", "quantity": 2}, {"sku": "apple", "quantity": 2}])
        self.assertEqual(stock, {"apple": 3})

    def test_invalid_line_leaves_stock_unchanged(self):
        stock = {"apple": 5}
        with self.assertRaisesRegex(ValueError, "positive integer"):
            reserve_all(stock, [{"sku": "apple", "quantity": 2}, {"sku": "apple", "quantity": 0}])
        self.assertEqual(stock, {"apple": 5})
