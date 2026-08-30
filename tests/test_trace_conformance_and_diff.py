import unittest

from regression_lab.schema import trace_conformance
from regression_lab.trace_diff import compare_traces


def trace(*names):
    events = []
    sequence = 1
    for index, name in enumerate(names):
        span_id = f"span-{index}"
        events.append({"kind": "span_start", "span_id": span_id, "parent_span_id": None if index == 0 else "span-0", "name": name, "span_type": "agent" if index == 0 else "workflow", "trace_id": "trace", "event_seq": sequence, "ts": float(sequence), "attributes": {}})
        sequence += 1
    for index, _ in reversed(list(enumerate(names))):
        span_id = f"span-{index}"
        events.append({"kind": "span_end", "span_id": span_id, "trace_id": "trace", "event_seq": sequence, "ts": float(sequence), "status": "ok", "attributes": {}})
        sequence += 1
    return events


class TraceConformanceAndDiffTests(unittest.TestCase):
    def test_langgraph_requires_workflow_span(self):
        self.assertFalse(trace_conformance(trace("agent.run"), "langgraph")["valid"])
        self.assertTrue(trace_conformance(trace("agent.run", "workflow.plan"), "langgraph")["valid"])

    def test_trace_diff_finds_first_structural_change_without_span_ids(self):
        baseline = trace("agent.run", "workflow.plan")
        candidate = trace("agent.run", "workflow.execute")
        diff = compare_traces(baseline, candidate)
        self.assertEqual(diff["alignment"], "ordered_sibling_lcs")
        self.assertEqual(diff["first_divergence"]["kind"], "removed")
        self.assertEqual(diff["critical_path"]["baseline"]["precision"], "approximate")
