import json
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from regression_lab.schema import span_type_for, validate_events, validate_trace


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

    def test_expected_root_attributes_reject_identity_override(self):
        records = [
            event(1, "span_start", span_id="root", parent_span_id=None, name="agent.run", attributes={"trial_id": "new", "adapter_id": "wrong", "agent_version": "v1"}),
            event(2, "span_end", span_id="root", status="ok", attributes={}),
        ]
        validation = validate_events(records, expected_trial_id="new", expected_root_attributes={"adapter_id": "external-command", "agent_version": "v1"})
        self.assertFalse(validation.valid)
        self.assertTrue(any("adapter_id" in error for error in validation.errors))

    def test_v0_span_type_is_inferred_and_invalid_v1_type_is_rejected(self):
        legacy = event(1, "span_start", span_id="root", parent_span_id=None, name="model.call", attributes={})
        invalid = event(2, "span_start", span_id="child", parent_span_id="root", name="agent.step", span_type="model", attributes={})
        records = [legacy, invalid, event(3, "span_end", span_id="child", status="ok", attributes={}), event(4, "span_end", span_id="root", status="ok", attributes={})]

        validation = validate_events(records)

        self.assertEqual(span_type_for(legacy), "llm")
        self.assertEqual(span_type_for({"name": "custom.operation"}), "other")
        self.assertFalse(validation.valid)
        self.assertTrue(any("unsupported span_type" in error for error in validation.errors))


if __name__ == "__main__":
    unittest.main()
