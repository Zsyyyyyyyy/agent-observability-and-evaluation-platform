import unittest

from src.service import welcome


class GreetingServiceTests(unittest.TestCase):
    def test_missing_name_uses_guest(self):
        self.assertEqual(welcome({}), "Hello, Guest!")

    def test_existing_name_still_works(self):
        self.assertEqual(welcome({"name": "Ada"}), "Hello, Ada!")


if __name__ == "__main__":
    unittest.main()
