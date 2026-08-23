import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.behavior_diff import behavior_deltas, snapshot_trial_behavior


class BehaviorDiffTests(unittest.TestCase):
    def test_snapshot_tolerates_malformed_trace_attributes(self):
        with TemporaryDirectory() as directory:
            trace_path = Path(directory) / "trace.jsonl"
            trace_path.write_text(
                '\n'.join([
                    json.dumps({"kind": "span_start", "event_seq": 1, "span_id": "model", "name": "model.call", "attributes": []}),
                    json.dumps({"kind": "span_end", "event_seq": 2, "span_id": "model", "status": "ok", "attributes": []}),
                ]),
                encoding="utf-8",
            )

            snapshot = snapshot_trial_behavior({"trace_path": str(trace_path), "adapter_id": "external-command"})

        self.assertEqual(snapshot["model_calls"], 1)
        self.assertIsNone(snapshot["total_tokens"])

    def _result(self, directory: Path, *, model_calls: int, tool_calls: list[tuple[str, str]], duration_ms: int) -> dict:
        events = []
        sequence = 0
        for index in range(model_calls):
            span_id = f"model_{index}"
            sequence += 1
            events.append({"kind": "span_start", "event_seq": sequence, "span_id": span_id, "name": "model.call", "span_type": "llm", "attributes": {}})
            sequence += 1
            events.append({"kind": "span_end", "event_seq": sequence, "span_id": span_id, "status": "ok", "attributes": {"usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}})
        for index, (tool_name, target_path) in enumerate(tool_calls):
            span_id = f"tool_{index}"
            sequence += 1
            events.append({"kind": "span_start", "event_seq": sequence, "span_id": span_id, "name": "tool.call", "span_type": "tool", "attributes": {"tool_name": tool_name, "target_path": target_path, "argument_fingerprint": f"sha256:{tool_name}:{target_path}"}})
            sequence += 1
            events.append({"kind": "span_end", "event_seq": sequence, "span_id": span_id, "status": "ok", "attributes": {}})
        trace_path = directory / "trace.jsonl"
        trace_path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
        return {"trace_path": str(trace_path), "adapter_id": "external-command", "scores": [{"evaluator": "budget", "actual": {"duration_ms": duration_ms}}]}

    def test_snapshot_uses_trace_and_result_artifact(self):
        with TemporaryDirectory() as directory:
            result = self._result(Path(directory), model_calls=2, tool_calls=[("read_file", "src/a.py"), ("read_file", "src/a.py"), ("edit_file", "src/a.py")], duration_ms=21000)
            snapshot = snapshot_trial_behavior(result)

        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(snapshot["model_calls"], 2)
        self.assertEqual(snapshot["tool_calls"], 3)
        self.assertEqual(snapshot["tool_breakdown"], {"read_file": 2, "edit_file": 1})
        self.assertEqual(snapshot["duplicate_reads"], 1)
        self.assertEqual(snapshot["input_tokens"], 200)
        self.assertEqual(snapshot["output_tokens"], 40)
        self.assertEqual(snapshot["total_tokens"], 240)
        self.assertEqual(snapshot["duration_ms"], 21000)

    def test_pairs_snapshots_by_case_and_trial_index(self):
        baseline = {"schema_version": 1, "model_calls": 5, "tool_calls": 7, "tool_success_rate": 1.0, "duplicate_reads": 2, "repeated_tool_calls": 1, "input_tokens": 12000, "output_tokens": 1800, "total_tokens": 13800, "duration_ms": 21000}
        candidate = {**baseline, "model_calls": 3, "tool_calls": 4, "duplicate_reads": 0, "total_tokens": 2140, "duration_ms": 9790}
        diff = behavior_deltas(
            [{"case_id": "dependency-cycle", "trial_index": 1, "behavior_snapshot": baseline}],
            [{"case_id": "dependency-cycle", "trial_index": 1, "behavior_snapshot": candidate}],
            baseline_version="v3", candidate_version="v4.1",
        )

        delta = diff["deltas"][0]
        self.assertEqual(delta["baseline_version"], "v3")
        self.assertEqual(delta["candidate_version"], "v4.1")
        self.assertEqual(delta["delta"]["model_calls"], -2)
        self.assertEqual(delta["delta"]["tool_calls"], -3)
        self.assertEqual(delta["delta"]["duplicate_reads"], -2)
        self.assertEqual(delta["delta"]["total_tokens"], -11660)
        self.assertEqual(delta["delta"]["duration_ms"], -11210)

    def test_snapshot_detects_stable_semantic_patterns(self):
        def start(sequence, span_id, name, span_type, **attrs):
            return {"kind": "span_start", "event_seq": sequence, "span_id": span_id, "name": name, "span_type": span_type, "attributes": attrs}

        def end(sequence, span_id, status="ok"):
            return {"kind": "span_end", "event_seq": sequence, "span_id": span_id, "status": status, "attributes": {}}

        events = [
            start(1, "read_1", "tool.call", "tool", tool_name="read_file", target_path="src/a.py", argument_fingerprint="sha256:read"), end(2, "read_1"),
            start(3, "read_2", "tool.call", "tool", tool_name="read_file", target_path="src/a.py", argument_fingerprint="sha256:read"), end(4, "read_2"),
            start(5, "edit_1", "tool.call", "tool", tool_name="edit_file", argument_fingerprint="sha256:edit"), end(6, "edit_1", "error"),
            start(7, "edit_2", "tool.call", "tool", tool_name="edit_file", argument_fingerprint="sha256:edit"), end(8, "edit_2"),
            start(9, "bash", "tool.call", "tool", tool_name="bash", argument_fingerprint="sha256:bash"), end(10, "bash", "denied"),
            start(11, "test_1", "test.run", "test"), end(12, "test_1"),
            start(13, "test_2", "test.run", "test"), end(14, "test_2"),
            start(15, "final", "agent.finalize", "agent"), end(16, "final"),
            start(17, "late_model", "model.call", "llm"), end(18, "late_model"),
        ]
        with TemporaryDirectory() as directory:
            trace_path = Path(directory) / "trace.jsonl"
            trace_path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
            snapshot = snapshot_trial_behavior({"trace_path": str(trace_path), "adapter_id": "external-command", "adapter_capabilities": {
                "schema_version": 2, "trace": True, "hierarchical_trace": True, "model_usage": True,
                "tool_trace": True, "tool_semantics": True, "test_trace": True, "context_trace": False,
                "workflow_trace": False, "mcp_trace": False,
            }})

        self.assertEqual(snapshot["patterns"], {
            "duplicate_read": 1,
            "repeated_tool_call": 2,
            "tool_retry": 1,
            "failed_tool_call": 1,
            "denied_tool_call": 1,
            "test_retry": 1,
            "post_terminal_call": 1,
        })

    def test_pattern_deltas_list_added_and_removed_patterns(self):
        patterns = {"duplicate_read": 2, "repeated_tool_call": 1, "tool_retry": 0, "failed_tool_call": 0, "denied_tool_call": 0, "test_retry": 0, "post_terminal_call": 0}
        baseline = {"schema_version": 1, "patterns": patterns}
        candidate = {"schema_version": 1, "patterns": {**patterns, "duplicate_read": 0, "repeated_tool_call": 0, "tool_retry": 1}}
        diff = behavior_deltas(
            [{"case_id": "case", "trial_index": 1, "behavior_snapshot": baseline}],
            [{"case_id": "case", "trial_index": 1, "behavior_snapshot": candidate}],
        )

        self.assertEqual(diff["removed_patterns"], [
            {"pattern": "duplicate_read", "delta": -2},
            {"pattern": "repeated_tool_call", "delta": -1},
        ])
        self.assertEqual(diff["added_patterns"], [{"pattern": "tool_retry", "delta": 1}])

    def test_aggregates_trial_deltas_to_cases_and_experiment_summary(self):
        def snapshot(model_calls, duplicate_reads):
            return {
                "schema_version": 1,
                "model_calls": model_calls,
                "tool_calls": 4,
                "tool_success_rate": 1.0,
                "duplicate_reads": duplicate_reads,
                "repeated_tool_calls": 0,
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "duration_ms": 100,
                "patterns": {"duplicate_read": duplicate_reads, "repeated_tool_call": 0, "tool_retry": 0, "failed_tool_call": 0, "denied_tool_call": 0, "test_retry": 0, "post_terminal_call": 0},
            }

        baseline = [
            {"case_id": "case_a", "trial_index": 1, "behavior_snapshot": snapshot(5, 2)},
            {"case_id": "case_a", "trial_index": 2, "behavior_snapshot": snapshot(5, 2)},
            {"case_id": "case_b", "trial_index": 1, "behavior_snapshot": snapshot(3, 0)},
        ]
        candidate = [
            {"case_id": "case_a", "trial_index": 1, "behavior_snapshot": snapshot(3, 0)},
            {"case_id": "case_a", "trial_index": 2, "behavior_snapshot": snapshot(3, 0)},
            {"case_id": "case_b", "trial_index": 1, "behavior_snapshot": snapshot(3, 0)},
        ]
        diff = behavior_deltas(baseline, candidate)

        case_a = next(case for case in diff["case_diffs"] if case["case_id"] == "case_a")
        self.assertEqual(case_a["metrics"]["model_calls"], {"median_delta": -2.0, "classification": "improved", "available_trial_count": 2})
        self.assertEqual(case_a["patterns"]["duplicate_read"], {"delta": -4, "classification": "improved", "available_trial_count": 2})
        self.assertEqual(diff["summary"]["metrics"]["model_calls"], {"median_delta": -1.0, "improved_cases": 1, "unchanged_cases": 1, "regressed_cases": 0, "available_case_count": 2})
        self.assertEqual(diff["summary"]["patterns"]["duplicate_read"], {"improved_cases": 1, "unchanged_cases": 1, "regressed_cases": 0, "available_case_count": 2})
        self.assertTrue(diff["diagnostic_only"])
        self.assertTrue(diff["availability"]["model_calls"])
