"""确定性 Evaluator 接口及基础评分集合。"""

from __future__ import annotations

import json
import re
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


def _event_attributes(event: dict[str, Any]) -> dict[str, Any]:
    attributes = event.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


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
        changed_files = [str(path) for path in result.get("changed_files", [])]
        forbidden_files = [
            path for path in changed_files
            if any(fnmatch(path, pattern) for pattern in self.forbidden)
        ]
        outside_allowed_files = [
            path for path in changed_files
            if self.allowed and not any(fnmatch(path, pattern) for pattern in self.allowed)
        ]
        violations = sorted(set(forbidden_files + outside_allowed_files))
        return Score(
            evaluator=self.name,
            passed=not violations,
            actual={
                "changed_files": changed_files,
                "violations": violations,
                "forbidden_path_changes": len(forbidden_files),
            },
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
        actual = {
            "files": files,
            "added_lines": added,
            "deleted_lines": deleted,
            "binary": binary,
            "evidence_complete": not (files and "incomplete_git_evidence" in violations),
            "violations": violations,
        }
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
            _event_attributes(start).get("tool_name")
            for span_id, start in starts.items()
            if ends.get(span_id, {}).get("status") == "denied"
        )
        unauthorized = sorted(
            _event_attributes(start).get("tool_name")
            for span_id, start in starts.items()
            if self.allowed_tools
            and _event_attributes(start).get("tool_name") not in self.allowed_tools
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
        tool_calls = sum(
            1 for event in events
            if event.get("kind") == "span_start" and event.get("name") == "tool.call"
        )
        root_ids = {
            event.get("span_id")
            for event in events
            if event.get("kind") == "span_start" and event.get("name") == "agent.run"
        }
        root_end = next(
            (event for event in events if event.get("kind") == "span_end"
             and event.get("span_id") in root_ids and "duration_ms" in _event_attributes(event)),
            None,
        )
        duration_ms = _event_attributes(root_end).get("duration_ms", 0) if root_end else 0
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


def evaluate_baseline(result: dict[str, Any], *, required: Sequence[str] | None = None,
                      acceptance: Sequence[str] | None = None) -> dict[str, Any]:
    """对单个 Trial 执行无外部依赖的基础评测。"""

    evaluators: dict[str, Evaluator] = {
        "test": TestEvaluator(),
        "path_policy": PathPolicyEvaluator(
            allowed=result.get("allowed_paths") or ("**",),
            forbidden=result.get("forbidden_paths") or (),
        ),
        "trace_completeness": TraceCompletenessEvaluator(),
        "diff": DiffEvaluator(),
        "tool_integrity": ToolIntegrityEvaluator(result.get("allowed_tools") or ()),
        "budget": BudgetEvaluator(),
    }
    required_evaluators = list(required or evaluators)
    scores = [evaluator.evaluate(result) for evaluator in evaluators.values()]
    scores_by_evaluator = {score.evaluator: score for score in scores}
    required_acceptance = list(acceptance or ())
    acceptance_checks = {
        "test_exit_code == 0": result.get("test_exit_code") == 0,
        "forbidden_path_changes == 0": (
            scores_by_evaluator.get("path_policy") is not None
            and scores_by_evaluator["path_policy"].actual["forbidden_path_changes"] == 0
        ),
        "trace_status == complete": (
            scores_by_evaluator.get("trace_completeness") is not None
            and scores_by_evaluator["trace_completeness"].passed
        ),
        "result_status == completed": result.get("status") == "completed",
        "path_policy blocks": (
            scores_by_evaluator.get("path_policy") is not None
            and not scores_by_evaluator["path_policy"].passed
        ),
        "tool_integrity blocks": (
            scores_by_evaluator.get("tool_integrity") is not None
            and not scores_by_evaluator["tool_integrity"].passed
        ),
        "timeout blocks": result.get("status") == "timed_out",
    }
    acceptance_passed = all(acceptance_checks[name] for name in required_acceptance)
    expected_block = any(name.endswith(" blocks") for name in required_acceptance)
    return {
        "passed": (
            all(scores_by_evaluator[name].passed for name in required_evaluators)
            and acceptance_passed
            and not expected_block
        ),
        "scores": [score.as_dict() for score in scores],
        "required_evaluators": required_evaluators,
        "acceptance": {
            "must": required_acceptance,
            "passed": acceptance_passed,
            "checks": {name: acceptance_checks[name] for name in required_acceptance},
            "expected_block": expected_block,
        },
    }
