import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from regression_lab.schema import validate_trace
from regression_lab_observer.langgraph import LangGraphObserver


class LangGraphObserverTests(unittest.TestCase):
    def _environment(self, root: Path) -> dict[str, str]:
        return {
            "REGRESSION_TRACE_PATH": str(root / "trace.jsonl"),
            "REGRESSION_TRACE_ID": "trace_langgraph",
            "REGRESSION_TRIAL_ID": "trial_langgraph",
            "REGRESSION_CASE_ID": "case_langgraph",
            "REGRESSION_AGENT_VERSION": "v2",
            "REGRESSION_ADAPTER_ID": "external-command",
            "REGRESSION_OBSERVATION_STATUS_PATH": str(root / "observation-status.json"),
        }

    def test_callback_builds_a_closed_hierarchical_trace_without_payloads(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.dict(os.environ, self._environment(root), clear=False):
                with LangGraphObserver.from_environment() as observation:
                    callback = observation.callback
                    callback.on_chain_start({}, {}, run_id="graph", metadata={})
                    callback.on_chain_start({}, {}, run_id="node", parent_run_id="graph", metadata={"langgraph_node": "Coder"})
                    callback.on_chat_model_start({"kwargs": {"model_name": "safe-model"}}, [["secret prompt"]], run_id="model", parent_run_id="node")
                    callback.on_llm_end({"llm_output": {"token_usage": {"prompt_tokens": 8, "completion_tokens": 3}}}, run_id="model")
                    callback.on_tool_start({"name": "write_file"}, "secret tool arguments", run_id="tool", parent_run_id="node")
                    callback.on_tool_end("secret tool output", run_id="tool")
                    callback.on_chain_end({}, run_id="node")
                    callback.on_chain_end({}, run_id="graph")
            events = [json.loads(line) for line in (root / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
            status = json.loads((root / "observation-status.json").read_text(encoding="utf-8"))
            validation = validate_trace(root / "trace.jsonl", expected_trace_id="trace_langgraph", expected_trial_id="trial_langgraph", expected_root_attributes={"agent_version": "v2", "adapter_id": "external-command"})
            self.assertTrue(validation.valid, validation.errors)
            self.assertEqual(status, {"complete": True, "errors": []})
            starts = [event for event in events if event["kind"] == "span_start"]
            self.assertEqual([event["name"] for event in starts], ["agent.run", "workflow.Coder", "model.call", "tool.call"])
            self.assertEqual(starts[2]["attributes"]["model"], "safe-model")
            self.assertEqual(starts[3]["attributes"]["tool_name"], "write_file")
            self.assertNotIn("secret prompt", json.dumps(events))
            self.assertNotIn("secret tool arguments", json.dumps(events))
            self.assertNotIn("secret tool output", json.dumps(events))
            model_end = next(event for event in events if event.get("span_id") == starts[2]["span_id"] and event["kind"] == "span_end")
            self.assertEqual(model_end["attributes"]["usage"]["total_tokens"], 11)

    def test_exception_closes_the_root_and_marks_framework_observation_incomplete(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.dict(os.environ, self._environment(root), clear=False):
                with self.assertRaisesRegex(RuntimeError, "agent failed"):
                    with LangGraphObserver.from_environment() as observation:
                        observation.callback.on_chain_start({}, {}, run_id="node", metadata={"langgraph_node": "Planner"})
                        raise RuntimeError("agent failed")
            status = json.loads((root / "observation-status.json").read_text(encoding="utf-8"))
            validation = validate_trace(root / "trace.jsonl", expected_trace_id="trace_langgraph", expected_trial_id="trial_langgraph")
            self.assertTrue(validation.valid, validation.errors)
            self.assertFalse(status["complete"])


if __name__ == "__main__":
    unittest.main()
