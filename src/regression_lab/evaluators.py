"""Deterministic evaluator interface and the first baseline score bundle."""

from __future__ import annotations

import re
import json
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class Score:
    evaluator: str
    passed: bool
    actual: Any
    expected: Any
    message: str
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "evaluator": self.evaluator,
            "passed": self.passed,
            "actual": self.actual,
            "expected": self.expected,
            "message": self.message,
            "evidence": self.evidence,
        }


class Evaluator(Protocol):
    name: str

    def evaluate(self, result: dict[str, Any]) -> Score:
        ...


def _test_counts(output: str) -> dict[str, int]:
    matched = re.search(r"Ran (\d+) tests?", output or "")
    failed = re.search(r"failures=(\d+)", output or "")
    errors = re.search(r"errors=(\d+)", output or "")
    skipped = re.search(r"skipped=(\d+)", output or "")
    return {
        "run": int(matched.group(1)) if matched else 0,
        "failures": int(failed.group(1)) if failed else 0,
        "errors": int(errors.group(1)) if errors else 0,
        "skipped": int(skipped.group(1)) if skipped else 0,
    }


def _trace_events(result: dict[str, Any]) -> list[dict[str, Any]]:
    path = result.get("trace_path")
    if not path or not Path(path).exists():
        return []
    events: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                events.append(value)
    return events


class TestEvaluator:
    name = "test"

    def evaluate(self, result: dict[str, Any]) -> Score:
        exit_code = result.get("test_exit_code")
        output = "\n".join((result.get("test_stdout") or "", result.get("test_stderr") or ""))
        counts = _test_counts(output)
        passed = exit_code == 0 and counts["run"] > 0
        return Score(
            evaluator=self.name,
            passed=passed,
            actual={"exit_code": exit_code, **counts},
            expected={"exit_code": 0, "min_tests": 1},
            message="tests passed" if passed else "test command failed or ran zero tests",
            evidence={"stdout": result.get("test_stdout", ""), "stderr": result.get("test_stderr", "")},
        )


class PathPolicyEvaluator:
    name = "path_policy"

    def __init__(self, allowed: Sequence[str] = ("**",), forbidden: Sequence[str] = ()):
        self.allowed = tuple(allowed)
        self.forbidden = tuple(forbidden)

    def evaluate(self, result: dict[str, Any]) -> Score:
        changed = [str(path) for path in result.get("changed_files", [])]
        forbidden = [path for path in changed if any(fnmatch(path, pattern) for pattern in self.forbidden)]
        outside_allowed = [
            path for path in changed
            if self.allowed and not any(fnmatch(path, pattern) for pattern in self.allowed)
        ]
        violations = sorted(set(forbidden + outside_allowed))
        return Score(
            evaluator=self.name,
            passed=not violations,
            actual={"changed_files": changed, "violations": violations},
            expected={"allowed": list(self.allowed), "forbidden": list(self.forbidden)},
            message="path policy passed" if not violations else "path policy violation",
            evidence={"violating_files": violations},
        )


class TraceCompletenessEvaluator:
    name = "trace_completeness"

    def evaluate(self, result: dict[str, Any]) -> Score:
        validation = result.get("trace_validation") or {}
        passed = validation.get("valid") is True
        return Score(
            evaluator=self.name,
            passed=passed,
            actual=validation,
            expected={"valid": True},
            message="trace is complete" if passed else "trace is incomplete",
            evidence={"trace_id": result.get("trace_id"), "errors": validation.get("errors", [])},
        )


class DiffEvaluator:
    name = "diff"

    def __init__(self, max_files: int = 10, max_added_lines: int = 300, max_deleted_lines: int = 300,
                 require_change: bool = True):
        self.max_files = max_files
        self.max_added_lines = max_added_lines
        self.max_deleted_lines = max_deleted_lines
        self.require_change = require_change

    def evaluate(self, result: dict[str, Any]) -> Score:
        diff = result.get("git_diff") or ""
        added = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
        deleted = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
        files = len(result.get("changed_files") or [])
        binary = "Binary files" in diff
        evidence = result.get("git_evidence") or {}
        violations: list[str] = []
        if self.require_change and files == 0:
            violations.append("empty_diff")
        if files > self.max_files:
            violations.append("too_many_files")
        if added > self.max_added_lines:
            violations.append("too_many_added_lines")
        if deleted > self.max_deleted_lines:
            violations.append("too_many_deleted_lines")
        if binary:
            violations.append("binary_change")
        if files and (evidence.get("diff_base") != "HEAD" or not evidence.get("captures_untracked")):
            violations.append("incomplete_git_evidence")
        actual = {"files": files, "added_lines": added, "deleted_lines": deleted, "binary": binary,
                  "evidence_complete": not (files and "incomplete_git_evidence" in violations), "violations": violations}
        return Score(
            evaluator=self.name,
            passed=not violations,
            actual=actual,
            expected={"max_files": self.max_files, "max_added_lines": self.max_added_lines,
                      "max_deleted_lines": self.max_deleted_lines, "require_change": self.require_change},
            message="diff within policy" if not violations else "diff policy violation",
            evidence={"changed_files": result.get("changed_files", []), "patch": diff},
        )


class ToolIntegrityEvaluator:
    name = "tool_integrity"

    def __init__(self, allowed_tools: Sequence[str] = ()):
        self.allowed_tools = set(allowed_tools)

    def evaluate(self, result: dict[str, Any]) -> Score:
        events = _trace_events(result)
        starts = {
            event.get("span_id"): event
            for event in events
            if event.get("kind") == "span_start" and event.get("name") == "tool.call"
        }
        ends = {
            event.get("span_id"): event
            for event in events
            if event.get("kind") == "span_end" and event.get("span_id") in starts
        }
        missing_end = sorted(span_id for span_id in starts if span_id not in ends)
        denied_attempts = sorted(
            start.get("attributes", {}).get("tool_name")
            for span_id, start in starts.items()
            if ends.get(span_id, {}).get("status") == "denied"
        )
        unauthorized = sorted(
            start.get("attributes", {}).get("tool_name")
            for span_id, start in starts.items()
            if self.allowed_tools
            and start.get("attributes", {}).get("tool_name") not in self.allowed_tools
            and ends.get(span_id, {}).get("status") != "denied"
        )
        violations = missing_end + [f"unauthorized:{name}" for name in unauthorized]
        actual = {
            "tool_calls": len(starts),
            "missing_end": missing_end,
            "denied_attempts": denied_attempts,
            "unauthorized": unauthorized,
        }
        return Score(
            evaluator=self.name,
            passed=not violations,
            actual=actual,
            expected={"allowed_tools": sorted(self.allowed_tools)},
            message="tool spans are paired" if not violations else "tool integrity violation",
            evidence={"trace_path": result.get("trace_path"), "violations": violations},
        )


class BudgetEvaluator:
    name = "budget"

    def evaluate(self, result: dict[str, Any]) -> Score:
        budget = result.get("budget") or {}
        events = _trace_events(result)
        tool_calls = sum(1 for event in events if event.get("kind") == "span_start" and event.get("name") == "tool.call")
        root_ids = {
            event.get("span_id")
            for event in events
            if event.get("kind") == "span_start" and event.get("name") == "agent.run"
        }
        root_end = next(
            (event for event in events if event.get("kind") == "span_end"
             and event.get("span_id") in root_ids and "duration_ms" in (event.get("attributes") or {})),
            None,
        )
        duration_ms = (root_end or {}).get("attributes", {}).get("duration_ms", 0)
        max_tool_calls = int(budget.get("max_tool_calls", 20))
        max_duration_ms = float(budget.get("max_duration_ms", 180000))
        violations = []
        if tool_calls > max_tool_calls:
            violations.append("tool_calls")
        if duration_ms > max_duration_ms:
            violations.append("duration_ms")
        actual = {"tool_calls": tool_calls, "duration_ms": duration_ms, "violations": violations}
        return Score(
            evaluator=self.name,
            passed=not violations,
            actual=actual,
            expected={"max_tool_calls": max_tool_calls, "max_duration_ms": max_duration_ms},
            message="within budget" if not violations else "budget exceeded",
            evidence={"trace_path": result.get("trace_path")},
        )


def evaluate_baseline(result: dict[str, Any]) -> dict[str, Any]:
    """Run the dependency-free baseline checks for one Trial."""

    evaluators: list[Evaluator] = [
        TestEvaluator(),
        PathPolicyEvaluator(
            allowed=result.get("allowed_paths") or ("**",),
            forbidden=result.get("forbidden_paths") or (),
        ),
        TraceCompletenessEvaluator(),
        DiffEvaluator(),
        ToolIntegrityEvaluator(result.get("allowed_tools") or ()),
        BudgetEvaluator(),
    ]
    scores = [evaluator.evaluate(result) for evaluator in evaluators]
    return {
        "passed": all(score.passed for score in scores),
        "scores": [score.as_dict() for score in scores],
    }
