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


def tree_trace(*nodes):
    events = []
    for sequence, (span_id, parent_span_id, name, span_type) in enumerate(nodes, start=1):
        events.append({"kind": "span_start", "span_id": span_id, "parent_span_id": parent_span_id, "name": name, "span_type": span_type, "trace_id": "trace", "event_seq": sequence, "ts": float(sequence), "attributes": {}})
    for sequence, (span_id, _, _, _) in enumerate(reversed(nodes), start=len(nodes) + 1):
        events.append({"kind": "span_end", "span_id": span_id, "trace_id": "trace", "event_seq": sequence, "ts": float(sequence), "status": "ok", "attributes": {}})
    return events


def annotated_trace(*nodes):
    events = []
    for sequence, node in enumerate(nodes, start=1):
        events.append({
            "kind": "span_start", "span_id": node["id"], "parent_span_id": node.get("parent"),
            "name": node["name"], "span_type": node["span_type"], "trace_id": "trace",
            "event_seq": sequence, "ts": node.get("start", float(sequence)),
            "attributes": node.get("attributes", {}),
        })
    for sequence, node in enumerate(reversed(nodes), start=len(nodes) + 1):
        events.append({
            "kind": "span_end", "span_id": node["id"], "trace_id": "trace", "event_seq": sequence,
            "ts": node.get("end", float(sequence)), "status": node.get("status", "ok"), "attributes": node.get("end_attributes", {}),
        })
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
        self.assertEqual(diff["first_divergence"]["kind"], "span_removed")
        self.assertEqual(diff["critical_path"]["baseline"]["precision"], "approximate")

    def test_matched_rows_include_display_safe_metric_deltas(self):
        baseline = trace("agent.run", "workflow.plan")
        candidate = trace("agent.run", "workflow.plan")
        candidate[-1]["ts"] = 9.0
        matched = next(row for row in compare_traces(baseline, candidate)["rows"] if row["kind"] == "matched")
        self.assertIn("duration_ms", matched["delta"])
        self.assertEqual(matched["baseline"]["tool_calls"], 0)

    def test_trace_diff_rows_preserve_nested_parent_relationships(self):
        baseline = tree_trace(("base-root", None, "agent.run", "agent"), ("base-plan", "base-root", "workflow.plan", "workflow"), ("base-model", "base-plan", "model.call", "model"))
        candidate = tree_trace(("candidate-root", None, "agent.run", "agent"), ("candidate-plan", "candidate-root", "workflow.plan", "workflow"), ("candidate-model", "candidate-plan", "model.call", "model"))
        diff = compare_traces(baseline, candidate)
        rows = {row["baseline"]["name"]: row for row in diff["rows"] if row["baseline"]}

        self.assertEqual(diff["schema_version"], 2)
        self.assertIsNone(rows["agent.run"]["parent_row_id"])
        self.assertEqual(rows["workflow.plan"]["parent_row_id"], rows["agent.run"]["row_id"])
        self.assertEqual(rows["model.call"]["parent_row_id"], rows["workflow.plan"]["row_id"])

    def test_added_and_removed_nodes_include_their_complete_subtrees(self):
        root_only = tree_trace(("root", None, "agent.run", "agent"))
        removed = tree_trace(("root", None, "agent.run", "agent"), ("legacy", "root", "workflow.legacy", "workflow"), ("legacy-tool", "legacy", "tool.call", "tool"))
        added = tree_trace(("root", None, "agent.run", "agent"), ("verify", "root", "workflow.verify", "workflow"), ("verify-tool", "verify", "tool.call", "tool"))

        removed_rows = compare_traces(removed, root_only)["rows"]
        added_rows = compare_traces(root_only, added)["rows"]
        removed_names = [row["baseline"]["name"] for row in removed_rows if row["kind"] == "removed"]
        added_names = [row["candidate"]["name"] for row in added_rows if row["kind"] == "added"]
        removed_parent = next(row for row in removed_rows if row["baseline"] and row["baseline"]["name"] == "workflow.legacy")
        removed_child = next(row for row in removed_rows if row["baseline"] and row["baseline"]["name"] == "tool.call")

        self.assertEqual(removed_names, ["workflow.legacy", "tool.call"])
        self.assertEqual(added_names, ["workflow.verify", "tool.call"])
        self.assertEqual(removed_child["parent_row_id"], removed_parent["row_id"])

    def test_first_divergence_uses_preorder_instead_of_later_sibling_order(self):
        baseline = tree_trace(("root", None, "agent.run", "agent"), ("plan", "root", "workflow.plan", "workflow"), ("legacy", "plan", "workflow.legacy", "workflow"), ("finish", "root", "workflow.finish", "workflow"))
        candidate = tree_trace(("root", None, "agent.run", "agent"), ("plan", "root", "workflow.plan", "workflow"), ("verify", "plan", "workflow.verify", "workflow"), ("finish", "root", "workflow.finish", "workflow"), ("later", "root", "workflow.later", "workflow"))
        diff = compare_traces(baseline, candidate)

        self.assertEqual(diff["first_divergence"]["path"][-1], "workflow.legacy")

    def test_repeated_siblings_and_multiple_runs_have_stable_alignment(self):
        baseline = tree_trace(("base-root", None, "agent.run", "agent"), ("base-first", "base-root", "workflow.step", "workflow"), ("base-second", "base-root", "workflow.step", "workflow"))
        candidate = tree_trace(("candidate-root", None, "agent.run", "agent"), ("candidate-first", "candidate-root", "workflow.step", "workflow"), ("candidate-second", "candidate-root", "workflow.step", "workflow"))
        first, second = compare_traces(baseline, candidate), compare_traces(baseline, candidate)

        self.assertEqual(first, second)
        self.assertEqual(sum(row["kind"] == "matched" for row in first["rows"]), 3)

    def test_first_divergence_keeps_nested_change_before_later_sibling_change(self):
        baseline = annotated_trace(
            {"id": "base-root", "name": "agent.run", "span_type": "agent"},
            {"id": "base-planner", "parent": "base-root", "name": "workflow.Planner", "span_type": "workflow"},
            {"id": "base-tool", "parent": "base-planner", "name": "tool.call", "span_type": "tool", "attributes": {"tool_name": "read_file"}},
            {"id": "base-legacy", "parent": "base-root", "name": "workflow.Legacy", "span_type": "workflow"},
        )
        candidate = annotated_trace(
            {"id": "candidate-root", "name": "agent.run", "span_type": "agent"},
            {"id": "candidate-planner", "parent": "candidate-root", "name": "workflow.Planner", "span_type": "workflow"},
            {"id": "candidate-tool", "parent": "candidate-planner", "name": "tool.call", "span_type": "tool", "attributes": {"tool_name": "read_file"}, "status": "error"},
            {"id": "candidate-verify", "parent": "candidate-root", "name": "workflow.Verify", "span_type": "workflow"},
        )
        diff = compare_traces(baseline, candidate)

        self.assertEqual(diff["ordering"], {"method": "aligned_causal_preorder", "parallel_precision": "partial_order"})
        self.assertEqual(diff["first_divergence"]["kind"], "status_changed")
        self.assertEqual(diff["first_divergence"]["path"], ["agent.run", "workflow.Planner", "tool.call(read_file)"])

    def test_operation_changes_are_matched_as_behavior_divergences(self):
        baseline = annotated_trace(
            {"id": "base-root", "name": "agent.run", "span_type": "agent"},
            {"id": "base-tool", "parent": "base-root", "name": "tool.call", "span_type": "tool", "attributes": {"tool_name": "read_file"}},
            {"id": "base-model", "parent": "base-root", "name": "model.call", "span_type": "llm", "attributes": {"model": "model-a"}},
        )
        candidate = annotated_trace(
            {"id": "candidate-root", "name": "agent.run", "span_type": "agent"},
            {"id": "candidate-tool", "parent": "candidate-root", "name": "tool.call", "span_type": "tool", "attributes": {"tool_name": "edit_file"}},
            {"id": "candidate-model", "parent": "candidate-root", "name": "model.call", "span_type": "llm", "attributes": {"model": "model-b"}},
        )
        diff = compare_traces(baseline, candidate)
        divergences = [row.get("divergence") for row in diff["rows"]]

        self.assertEqual(diff["first_divergence"]["kind"], "tool_changed")
        self.assertIn("tool_changed", divergences)
        self.assertIn("model_changed", divergences)

    def test_efficiency_differences_do_not_create_behavior_divergence(self):
        baseline = annotated_trace(
            {"id": "base-root", "name": "agent.run", "span_type": "agent", "end": 10.0},
            {"id": "base-model", "parent": "base-root", "name": "model.call", "span_type": "llm", "attributes": {"model": "model-a"}, "end_attributes": {"usage": {"total_tokens": 10}}},
        )
        candidate = annotated_trace(
            {"id": "candidate-root", "name": "agent.run", "span_type": "agent", "end": 20.0},
            {"id": "candidate-model", "parent": "candidate-root", "name": "model.call", "span_type": "llm", "attributes": {"model": "model-a"}, "end_attributes": {"usage": {"total_tokens": 20}}},
        )

        self.assertIsNone(compare_traces(baseline, candidate)["first_divergence"])

    def test_first_sibling_addition_and_middle_removal_keep_causal_order(self):
        baseline = tree_trace(("base-root", None, "agent.run", "agent"), ("base-plan", "base-root", "workflow.plan", "workflow"), ("base-legacy", "base-root", "workflow.legacy", "workflow"), ("base-finish", "base-root", "workflow.finish", "workflow"))
        candidate = tree_trace(("candidate-root", None, "agent.run", "agent"), ("candidate-prepare", "candidate-root", "workflow.prepare", "workflow"), ("candidate-plan", "candidate-root", "workflow.plan", "workflow"), ("candidate-finish", "candidate-root", "workflow.finish", "workflow"))
        diff = compare_traces(baseline, candidate)

        self.assertEqual(diff["first_divergence"]["kind"], "span_added")
        self.assertEqual(diff["first_divergence"]["path"], ["agent.run", "workflow.prepare"])
        self.assertEqual(
            [row["kind"] for row in diff["rows"] if row["depth"] == 1],
            ["added", "matched", "removed", "matched"],
        )

    def test_parallel_siblings_use_event_sequence_and_remain_deterministic(self):
        baseline = annotated_trace(
            {"id": "base-root", "name": "agent.run", "span_type": "agent"},
            {"id": "base-planner", "parent": "base-root", "name": "workflow.plan", "span_type": "workflow", "start": 100.0},
            {"id": "base-coder", "parent": "base-root", "name": "workflow.code", "span_type": "workflow", "start": 1.0},
        )
        candidate = annotated_trace(
            {"id": "candidate-root", "name": "agent.run", "span_type": "agent"},
            {"id": "candidate-planner", "parent": "candidate-root", "name": "workflow.plan", "span_type": "workflow", "start": 100.0},
            {"id": "candidate-coder", "parent": "candidate-root", "name": "workflow.code", "span_type": "workflow", "start": 1.0},
        )
        first, second = compare_traces(baseline, candidate), compare_traces(baseline, candidate)
        child_names = [row["baseline"]["name"] for row in first["rows"] if row["depth"] == 1]

        self.assertEqual(first, second)
        self.assertEqual(child_names, ["workflow.plan", "workflow.code"])

    def test_nested_status_changes_prefer_the_tool_cause(self):
        baseline = annotated_trace(
            {"id": "base-root", "name": "agent.run", "span_type": "agent"},
            {"id": "base-coder", "parent": "base-root", "name": "workflow.Coder", "span_type": "workflow"},
            {"id": "base-tool", "parent": "base-coder", "name": "tool.call", "span_type": "tool", "attributes": {"tool_name": "edit_file"}},
        )
        candidate = annotated_trace(
            {"id": "candidate-root", "name": "agent.run", "span_type": "agent", "status": "error"},
            {"id": "candidate-coder", "parent": "candidate-root", "name": "workflow.Coder", "span_type": "workflow", "status": "error"},
            {"id": "candidate-tool", "parent": "candidate-coder", "name": "tool.call", "span_type": "tool", "attributes": {"tool_name": "edit_file"}, "status": "error"},
        )
        diff = compare_traces(baseline, candidate)

        self.assertEqual(diff["first_divergence"]["kind"], "status_changed")
        self.assertEqual(diff["first_divergence"]["path"], ["agent.run", "workflow.Coder", "tool.call(edit_file)"])

    def test_root_status_change_is_reported_without_a_more_specific_child(self):
        baseline = annotated_trace({"id": "base-root", "name": "agent.run", "span_type": "agent"})
        candidate = annotated_trace({"id": "candidate-root", "name": "agent.run", "span_type": "agent", "status": "error"})

        diff = compare_traces(baseline, candidate)

        self.assertEqual(diff["first_divergence"]["kind"], "status_changed")
        self.assertEqual(diff["first_divergence"]["path"], ["agent.run"])

    def test_child_structure_change_precedes_parent_status_change(self):
        baseline = annotated_trace(
            {"id": "base-root", "name": "agent.run", "span_type": "agent"},
            {"id": "base-legacy", "parent": "base-root", "name": "workflow.Legacy", "span_type": "workflow"},
        )
        candidate = annotated_trace(
            {"id": "candidate-root", "name": "agent.run", "span_type": "agent", "status": "error"},
            {"id": "candidate-verify", "parent": "candidate-root", "name": "workflow.Verify", "span_type": "workflow"},
        )

        self.assertEqual(compare_traces(baseline, candidate)["first_divergence"]["kind"], "span_removed")

    def test_operation_changes_precede_their_status_change(self):
        baseline = annotated_trace(
            {"id": "base-root", "name": "agent.run", "span_type": "agent"},
            {"id": "base-tool", "parent": "base-root", "name": "tool.call", "span_type": "tool", "attributes": {"tool_name": "read_file"}},
            {"id": "base-model", "parent": "base-root", "name": "model.call", "span_type": "llm", "attributes": {"model": "model-a"}},
        )
        tool_candidate = annotated_trace(
            {"id": "candidate-root", "name": "agent.run", "span_type": "agent"},
            {"id": "candidate-tool", "parent": "candidate-root", "name": "tool.call", "span_type": "tool", "attributes": {"tool_name": "edit_file"}, "status": "error"},
            {"id": "candidate-model", "parent": "candidate-root", "name": "model.call", "span_type": "llm", "attributes": {"model": "model-a"}},
        )
        model_candidate = annotated_trace(
            {"id": "candidate-root", "name": "agent.run", "span_type": "agent"},
            {"id": "candidate-tool", "parent": "candidate-root", "name": "tool.call", "span_type": "tool", "attributes": {"tool_name": "read_file"}},
            {"id": "candidate-model", "parent": "candidate-root", "name": "model.call", "span_type": "llm", "attributes": {"model": "model-b"}, "status": "error"},
        )

        self.assertEqual(compare_traces(baseline, tool_candidate)["first_divergence"]["kind"], "tool_changed")
        self.assertEqual(compare_traces(baseline, model_candidate)["first_divergence"]["kind"], "model_changed")

    def test_missing_status_is_not_a_behavior_divergence(self):
        baseline = annotated_trace({"id": "base-root", "name": "agent.run", "span_type": "agent", "status": None})
        candidate = annotated_trace({"id": "candidate-root", "name": "agent.run", "span_type": "agent"})

        self.assertIsNone(compare_traces(baseline, candidate)["first_divergence"])

    def test_parallel_status_failures_use_event_sequence_deterministically(self):
        baseline = annotated_trace(
            {"id": "base-root", "name": "agent.run", "span_type": "agent"},
            {"id": "base-first", "parent": "base-root", "name": "workflow.First", "span_type": "workflow"},
            {"id": "base-second", "parent": "base-root", "name": "workflow.Second", "span_type": "workflow"},
        )
        candidate = annotated_trace(
            {"id": "candidate-root", "name": "agent.run", "span_type": "agent"},
            {"id": "candidate-first", "parent": "candidate-root", "name": "workflow.First", "span_type": "workflow", "status": "error"},
            {"id": "candidate-second", "parent": "candidate-root", "name": "workflow.Second", "span_type": "workflow", "status": "error"},
        )
        first, second = compare_traces(baseline, candidate), compare_traces(baseline, candidate)

        self.assertEqual(first, second)
        self.assertEqual(first["first_divergence"]["path"], ["agent.run", "workflow.First"])
