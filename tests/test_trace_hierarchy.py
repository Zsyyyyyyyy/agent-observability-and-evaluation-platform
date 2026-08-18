import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.schema import validate_events, validate_trace
from regression_lab.trace import TraceCollector


class TraceHierarchyTests(unittest.TestCase):
    def test_collector_records_v1_span_type(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            collector = TraceCollector(path, "trace_test")
            span_id = collector.start_span("agent.step", span_type="agent")
            collector.end_span(span_id)
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            validation = validate_trace(path)

        self.assertEqual(records[0]["span_type"], "agent")
        self.assertTrue(validation.valid, validation.errors)

    def test_multiple_roots_are_rejected(self):
        records = [
            {"trace_id": "trace_test", "event_seq": 1, "ts": 1.0, "kind": "span_start", "span_id": "root_1", "parent_span_id": None, "name": "agent.run", "span_type": "agent", "attributes": {}},
            {"trace_id": "trace_test", "event_seq": 2, "ts": 2.0, "kind": "span_start", "span_id": "root_2", "parent_span_id": None, "name": "agent.run", "span_type": "agent", "attributes": {}},
            {"trace_id": "trace_test", "event_seq": 3, "ts": 3.0, "kind": "span_end", "span_id": "root_1", "status": "ok", "attributes": {}},
            {"trace_id": "trace_test", "event_seq": 4, "ts": 4.0, "kind": "span_end", "span_id": "root_2", "status": "ok", "attributes": {}},
        ]

        validation = validate_events(records)

        self.assertFalse(validation.valid)
        self.assertTrue(any("multiple root" in error for error in validation.errors))
