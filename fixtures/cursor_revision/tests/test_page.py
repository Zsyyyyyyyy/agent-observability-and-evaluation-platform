import unittest

from src.page import list_page


class CursorRevisionTests(unittest.TestCase):
    def test_pages_are_contiguous_without_duplicates(self):
        first = list_page(["a", "b", "c", "d", "e"], "rev-7", 2)
        second = list_page(["a", "b", "c", "d", "e"], "rev-7", 2, first["next_cursor"])
        third = list_page(["a", "b", "c", "d", "e"], "rev-7", 2, second["next_cursor"])
        self.assertEqual(first, {"items": ["a", "b"], "next_cursor": "rev-7:2"})
        self.assertEqual(second, {"items": ["c", "d"], "next_cursor": "rev-7:4"})
        self.assertEqual(third, {"items": ["e"], "next_cursor": None})

    def test_cursor_from_another_revision_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cursor revision does not match"):
            list_page(["a", "b"], "rev-8", 1, "rev-7:1")

    def test_negative_or_out_of_range_offset_is_rejected(self):
        for cursor in ("rev-7:-1", "rev-7:4"):
            with self.subTest(cursor=cursor):
                with self.assertRaisesRegex(ValueError, "invalid cursor offset"):
                    list_page(["a", "b", "c"], "rev-7", 1, cursor)

    def test_malformed_cursor_and_invalid_limit_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid cursor"):
            list_page(["a"], "rev-7", 1, "broken")
        with self.assertRaisesRegex(ValueError, "positive integer"):
            list_page(["a"], "rev-7", 0)
