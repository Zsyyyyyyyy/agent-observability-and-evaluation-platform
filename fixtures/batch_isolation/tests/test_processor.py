import unittest

from src.processor import process_batch


class BatchIsolationTests(unittest.TestCase):
    def test_valid_rows_are_processed(self):
        self.assertEqual(
            process_batch([{"id": "a", "amount": 1}, {"id": "b", "amount": 2}]),
            {"accepted": [{"id": "a", "amount": 1}, {"id": "b", "amount": 2}], "rejected": []},
        )

    def test_invalid_row_does_not_discard_neighboring_valid_rows(self):
        result = process_batch([{"id": "a", "amount": 1}, {"id": "", "amount": 3}, {"id": "b", "amount": 0}])
        self.assertEqual(result["accepted"], [{"id": "a", "amount": 1}, {"id": "b", "amount": 0}])
        self.assertEqual(result["rejected"], [{"index": 1, "reason": "id is required"}])

    def test_multiple_failures_keep_original_indexes(self):
        result = process_batch([{"id": "", "amount": 1}, {"id": "ok", "amount": -1}])
        self.assertEqual(result["accepted"], [])
        self.assertEqual(
            result["rejected"],
            [{"index": 0, "reason": "id is required"}, {"index": 1, "reason": "amount must be a non-negative integer"}],
        )


if __name__ == "__main__":
    unittest.main()
