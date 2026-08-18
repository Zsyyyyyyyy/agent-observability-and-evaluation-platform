import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.behavior import aggregate_behavior, summarize_trial_behavior
from regression_lab.tool_semantics import semantic_tool_attributes


class BehaviorMetricTests(unittest.TestCase):
    def _result(self, root: Path, events: list[dict]) -> dict:
        trace = root / "trace.jsonl"
        trace.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
        return {"trace_path": str(trace), "adapter_id": "react-agent", "status": "completed", "evaluation": {"passed": True}, "test_exit_code": 0}

    def test_semantics_never_include_raw_file_content_or_command(self):
        attrs = semantic_tool_attributes(
            "edit_file",
            {"path": "src/app.py", "old_text": "secret-before", "new_text": "secret-after"},
            worktree=Path("/tmp/worktree"),
        )
        self.assertEqual(attrs["target_path"], "src/app.py")
        self.assertEqual(attrs["argument_keys"], ["new_text", "old_text", "path"])
        self.assertTrue(attrs["argument_fingerprint"].startswith("sha256:"))
        self.assertNotIn("secret", json.dumps(attrs))

    def test_behavior_counts_duplicate_reads_repeats_and_edit_before_read(self):
        def start(seq, span, tool, **attrs):
            return {"kind": "span_start", "event_seq": seq, "span_id": span, "name": "tool.call", "attributes": {"tool_name": tool, **attrs}}

        def end(seq, span, status):
            return {"kind": "span_end", "event_seq": seq, "span_id": span, "name": "tool.call", "status": status, "attributes": {}}

        events = [
            start(1, "one", "edit_file", target_path="src/a.py", argument_fingerprint="sha256:edit"), end(2, "one", "ok"),
            start(3, "two", "read_file", target_path="src/a.py", argument_fingerprint="sha256:read"), end(4, "two", "ok"),
            start(5, "three", "read_file", target_path="src/a.py", argument_fingerprint="sha256:read"), end(6, "three", "ok"),
            start(7, "four", "bash", argument_fingerprint="sha256:bash"), end(8, "four", "denied"),
            start(9, "five", "glob", argument_fingerprint="sha256:glob"), end(10, "five", "error"),
        ]
        with TemporaryDirectory() as directory:
            behavior = summarize_trial_behavior(self._result(Path(directory), events))

        self.assertEqual(behavior["tool_calls"], 5)
        self.assertEqual(behavior["denied_tool_attempts"], 1)
        self.assertEqual(behavior["edit_before_read_count"], 1)
        self.assertEqual(behavior["duplicate_read_rate"], 0.5)
        self.assertEqual(behavior["repeated_tool_call_rate"], 0.2)
        self.assertEqual(behavior["tool_success_rate"], 0.6)
        self.assertEqual(behavior["tool_error_rate"], 0.2)

    def test_missing_semantic_fields_stay_not_available(self):
        with TemporaryDirectory() as directory:
            behavior = summarize_trial_behavior(self._result(Path(directory), [
                {"kind": "span_start", "event_seq": 1, "span_id": "one", "name": "tool.call", "attributes": {"tool_name": "read_file"}},
                {"kind": "span_end", "event_seq": 2, "span_id": "one", "name": "tool.call", "status": "ok", "attributes": {}},
            ]))
        self.assertIsNone(behavior["duplicate_read_rate"])
        self.assertIsNone(behavior["repeated_tool_call_rate"])
        self.assertEqual(behavior["evidence_availability"]["duplicate_reads"], "supported_but_not_observed")
        self.assertEqual(behavior["evidence_availability"]["repeated_tool_calls"], "supported_but_not_observed")

    def test_capability_availability_distinguishes_supported_missing_unsupported_and_zero(self):
        start = {"kind": "span_start", "event_seq": 1, "span_id": "read", "name": "tool.call", "span_type": "tool", "attributes": {"tool_name": "read_file", "target_path": "src/a.py", "argument_fingerprint": "sha256:read"}}
        end = {"kind": "span_end", "event_seq": 2, "span_id": "read", "name": "tool.call", "status": "ok", "attributes": {}}
        with TemporaryDirectory() as directory:
            result = self._result(Path(directory), [start, end])
            observed_zero = summarize_trial_behavior(result)
            missing = summarize_trial_behavior({**result, "trace_path": str(Path(directory) / "missing.jsonl")})
            unsupported = summarize_trial_behavior({
                **result,
                "adapter_capabilities": {**result.get("adapter_capabilities", {}), "schema_version": 2,
                    "trace": True, "hierarchical_trace": True, "model_usage": True, "tool_trace": True,
                    "tool_semantics": False, "test_trace": False, "context_trace": False,
                    "workflow_trace": False, "mcp_trace": False},
            })

        self.assertEqual(observed_zero["duplicate_reads"], 0)
        self.assertEqual(observed_zero["evidence_availability"]["duplicate_reads"], "available")
        self.assertEqual(missing["evidence_availability"]["tool_trace"], "supported_but_not_observed")
        self.assertIsNone(missing["tool_calls"])
        self.assertEqual(unsupported["evidence_availability"]["duplicate_reads"], "unsupported")
        self.assertIsNone(unsupported["duplicate_reads"])

    def test_historical_artifact_uses_registry_fallback_or_explicit_unknown(self):
        with TemporaryDirectory() as directory:
            result = self._result(Path(directory), [])
            legacy = summarize_trial_behavior({**result, "adapter_id": "external-command"})
            unknown = summarize_trial_behavior({**result, "adapter_id": "removed-adapter"})

        self.assertEqual(legacy["capability_source"], "registry_fallback")
        self.assertEqual(legacy["evidence_availability"]["tool_trace"], "supported_but_not_observed")
        self.assertEqual(unknown["capability_source"], "historical_unknown")
        self.assertEqual(unknown["evidence_availability"]["tool_trace"], "unsupported")

    def test_aggregate_preserves_verification_coverage(self):
        aggregate = aggregate_behavior([
            {"status": "completed", "evaluation_passed": True, "test_passed": True, "behavior": {"tool_success_rate": 1.0, "tool_error_rate": 0.0, "denied_tool_attempts": 0, "availability": {}, "evidence_availability": {}}},
            {"status": "completed", "evaluation_passed": True, "test_passed": False, "behavior": {"tool_success_rate": 0.5, "tool_error_rate": 0.5, "denied_tool_attempts": 1, "availability": {}, "evidence_availability": {}}},
        ])
        self.assertEqual(aggregate["verification_coverage"], 0.5)
        self.assertEqual(aggregate["tool_success_rate"], 0.75)
        self.assertEqual(aggregate["denied_tool_attempts"], 1)


if __name__ == "__main__":
    unittest.main()
