"""Deterministic, evidence-backed Coding Agent behavior diagnostics."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _events(trace_path: str | Path | None) -> list[dict[str, Any]]:
    if not trace_path:
        return []
    try:
        lines = Path(trace_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def summarize_trial_behavior(result: dict[str, Any]) -> dict[str, Any]:
    """Summarize completed tool spans without inventing unavailable semantics."""

    starts: dict[str, dict[str, Any]] = {}
    ends: dict[str, dict[str, Any]] = {}
    for event in _events(result.get("trace_path")):
        span_id = event.get("span_id")
        if not isinstance(span_id, str):
            continue
        if event.get("kind") == "span_start" and event.get("name") == "tool.call":
            starts[span_id] = event
        elif event.get("kind") == "span_end":
            ends[span_id] = event

    calls = []
    for span_id, start in starts.items():
        attrs = start.get("attributes") if isinstance(start.get("attributes"), dict) else {}
        end = ends.get(span_id)
        calls.append({
            "sequence": start.get("event_seq", 0),
            "tool_name": attrs.get("tool_name"),
            "target_path": attrs.get("target_path"),
            "fingerprint": attrs.get("argument_fingerprint"),
            "status": end.get("status") if end else None,
        })
    calls.sort(key=lambda call: int(call["sequence"]) if isinstance(call["sequence"], int) else 0)
    closed = [call for call in calls if isinstance(call["status"], str)]
    statuses = Counter(call["status"] for call in closed)
    by_tool: dict[str, dict[str, int]] = {}
    for call in closed:
        name = call["tool_name"] if isinstance(call["tool_name"], str) else "unknown"
        bucket = by_tool.setdefault(name, {"calls": 0, "ok": 0, "error": 0, "denied": 0})
        bucket["calls"] += 1
        if call["status"] in {"ok", "error", "denied"}:
            bucket[str(call["status"])] += 1

    fingerprint_calls = [call for call in calls if isinstance(call["fingerprint"], str) and call["fingerprint"].startswith("sha256:")]
    duplicate_fingerprints = sum(count - 1 for count in Counter(call["fingerprint"] for call in fingerprint_calls).values() if count > 1)
    successful_reads = [call for call in closed if call["tool_name"] == "read_file" and call["status"] == "ok"]
    readable = [call for call in successful_reads if isinstance(call["target_path"], str)]
    duplicate_reads = sum(count - 1 for count in Counter(call["target_path"] for call in readable).values() if count > 1)
    reads_by_path: set[str] = set()
    edit_before_read = 0
    editable_calls = [call for call in calls if call["tool_name"] in {"edit_file", "write_file"}]
    semantic_path_available = bool(readable or any(isinstance(call["target_path"], str) for call in editable_calls))
    for call in calls:
        path = call["target_path"]
        if not isinstance(path, str):
            continue
        if call["tool_name"] == "read_file" and call["status"] == "ok":
            reads_by_path.add(path)
        elif call["tool_name"] in {"edit_file", "write_file"} and path not in reads_by_path:
            edit_before_read += 1

    return {
        "version": 1,
        "tool_calls": len(calls),
        "closed_tool_calls": len(closed),
        "tool_success_rate": statuses["ok"] / len(closed) if closed else None,
        "tool_error_rate": statuses["error"] / len(closed) if closed else None,
        "denied_tool_attempts": statuses["denied"],
        "tool_breakdown": by_tool,
        "repeated_tool_call_rate": duplicate_fingerprints / len(fingerprint_calls) if fingerprint_calls else None,
        "duplicate_read_rate": duplicate_reads / len(readable) if readable else None,
        "edit_before_read_count": edit_before_read if semantic_path_available else None,
        "test_retry_count": None,
        "availability": {
            "tool_outcomes": bool(closed),
            "repeated_tool_calls": bool(fingerprint_calls),
            "duplicate_reads": bool(readable),
            "edit_before_read": semantic_path_available,
            "test_retries": False,
        },
        "unavailable": {
            "test_retry_count": "test.run spans are not emitted in this SDK version",
            **({} if fingerprint_calls else {"repeated_tool_call_rate": "missing argument_fingerprint on tool spans"}),
            **({} if readable else {"duplicate_read_rate": "missing target_path on successful read_file spans"}),
            **({} if semantic_path_available else {"edit_before_read_count": "missing target_path on read/edit spans"}),
        },
    }


def aggregate_behavior(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate trial diagnostics while preserving explicit availability."""

    trial_behaviors = [job.get("behavior") for job in jobs if isinstance(job.get("behavior"), dict)]
    count = len(trial_behaviors)

    def average(field: str) -> float | None:
        values = [item[field] for item in trial_behaviors if isinstance(item.get(field), (int, float)) and not isinstance(item.get(field), bool)]
        return sum(values) / len(values) if values else None

    verified = [job for job in jobs if job.get("status") == "completed" and job.get("evaluation_passed") is True]
    covered = [job for job in verified if job.get("test_passed") is True]
    return {
        "trial_count": len(jobs),
        "instrumented_trial_count": count,
        "tool_success_rate": average("tool_success_rate"),
        "tool_error_rate": average("tool_error_rate"),
        "denied_tool_attempts": sum(int(item.get("denied_tool_attempts", 0)) for item in trial_behaviors),
        "repeated_tool_call_rate": average("repeated_tool_call_rate"),
        "duplicate_read_rate": average("duplicate_read_rate"),
        "edit_before_read_count": sum(int(item["edit_before_read_count"]) for item in trial_behaviors if isinstance(item.get("edit_before_read_count"), int)),
        "verification_coverage": len(covered) / len(verified) if verified else None,
        "availability": {
            "tool_outcomes": any(item.get("availability", {}).get("tool_outcomes") for item in trial_behaviors),
            "repeated_tool_calls": any(item.get("availability", {}).get("repeated_tool_calls") for item in trial_behaviors),
            "duplicate_reads": any(item.get("availability", {}).get("duplicate_reads") for item in trial_behaviors),
            "edit_before_read": any(item.get("availability", {}).get("edit_before_read") for item in trial_behaviors),
            "test_retries": False,
        },
        "unavailable": {
            "test_retry_count": "test.run spans are not emitted in this SDK version",
            **({} if count else {"all_behavior_metrics": "no Trial behavior summaries available"}),
        },
    }
