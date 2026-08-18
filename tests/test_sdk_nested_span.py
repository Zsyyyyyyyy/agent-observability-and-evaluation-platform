import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.schema import validate_trace
from regression_lab.sdk import AgentObserver


class AgentObserverNestedSpanTests(unittest.TestCase):
    def test_nested_spans_use_current_span_as_parent(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            observer = AgentObserver(path, "trace_test", trial_id="trial_test", case_id="case_test", agent_version="v1", adapter_id="test")
            with observer.run():
                with observer.span("agent.step", "agent"):
                    with observer.model_call(model="example-model"):
                        pass
                    with observer.tool_call("edit_file"):
                        pass
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            validation = validate_trace(path, expected_trial_id="trial_test")

        starts = [record for record in records if record["kind"] == "span_start"]
        by_name = {record["name"]: record for record in starts}
        self.assertEqual(by_name["agent.step"]["parent_span_id"], by_name["agent.run"]["span_id"])
        self.assertEqual(by_name["model.call"]["parent_span_id"], by_name["agent.step"]["span_id"])
        self.assertEqual(by_name["tool.call"]["parent_span_id"], by_name["agent.step"]["span_id"])
        self.assertEqual(by_name["model.call"]["span_type"], "llm")
        self.assertEqual(by_name["tool.call"]["span_type"], "tool")
        self.assertTrue(validation.valid, validation.errors)

    def test_exception_closes_nested_span_and_restores_parent(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            observer = AgentObserver(path, "trace_test", trial_id="trial_test", case_id="case_test", agent_version="v1", adapter_id="test")
            with observer.run():
                try:
                    with observer.span("agent.step", "agent"):
                        raise RuntimeError("expected")
                except RuntimeError:
                    pass
                with observer.model_call(model="example-model"):
                    pass
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            validation = validate_trace(path, expected_trial_id="trial_test")

        starts = [record for record in records if record["kind"] == "span_start"]
        step = next(record for record in starts if record["name"] == "agent.step")
        model = next(record for record in starts if record["name"] == "model.call")
        step_end = next(record for record in records if record["kind"] == "span_end" and record["span_id"] == step["span_id"])
        self.assertEqual(step_end["status"], "error")
        self.assertEqual(step_end["attributes"]["error_type"], "RuntimeError")
        self.assertEqual(model["parent_span_id"], next(record for record in starts if record["name"] == "agent.run")["span_id"])
        self.assertTrue(validation.valid, validation.errors)
