import json
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from regression_lab.schema import validate_events, validate_trace


def event(seq, kind, **fields):
    return {"trace_id": "trace_test", "event_seq": seq, "ts": 1.0 + seq, "kind": kind, **fields}


class TraceSchemaTests(unittest.TestCase):
    def test_valid_trace_has_closed_spans(self):
        records = [
            event(1, "span_start", span_id="span_1", parent_span_id=None, name="agent.run", attributes={}),
            event(2, "event", name="permission.check", parent_span_id="span_1", attributes={}),
            event(3, "span_end", span_id="span_1", status="ok", attributes={}),
        ]
        validation = validate_events(records)

        self.assertTrue(validation.valid)
        self.assertEqual(validation.span_count, 1)
        self.assertEqual(validation.errors, ())

    def test_missing_end_and_bad_sequence_are_rejected(self):
        records = [
            event(2, "span_start", span_id="span_1", parent_span_id=None, name="agent.run", attributes={}),
            event(2, "event", name="model.call", parent_span_id="span_1", attributes={}),
        ]
        validation = validate_events(records)

        self.assertFalse(validation.valid)
        self.assertTrue(any("strictly increasing" in error for error in validation.errors))
        self.assertTrue(any("missing end" in error for error in validation.errors))

    def test_validate_trace_reports_invalid_json(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text("{not-json}\n", encoding="utf-8")
            validation = validate_trace(path)

        self.assertFalse(validation.valid)
        self.assertEqual(validation.event_count, 0)
        self.assertTrue(any("invalid JSON" in error for error in validation.errors))

    def test_expected_trial_rejects_stale_trace_and_missing_parent(self):
        records = [
            event(1, "span_start", span_id="root", parent_span_id=None, name="agent.run", attributes={"trial_id": "old"}),
            event(2, "span_start", span_id="child", parent_span_id="missing", name="tool.call", attributes={}),
            event(3, "span_end", span_id="child", status="ok", attributes={}),
            event(4, "span_end", span_id="root", status="ok", attributes={}),
        ]
        validation = validate_events(records, expected_trace_id="trace_new", expected_trial_id="new")

        self.assertFalse(validation.valid)
        self.assertTrue(any("unexpected trace_id" in error for error in validation.errors))
        self.assertTrue(any("parent span" in error for error in validation.errors))
        self.assertTrue(any("trial_id" in error for error in validation.errors))


if __name__ == "__main__":
    unittest.main()
