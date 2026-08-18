"""Deterministic, evidence-backed Coding Agent behavior diagnostics."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from regression_lab.adapters import capabilities_for_result
from regression_lab.schema import span_type_for


EVIDENCE_AVAILABLE = "available"
EVIDENCE_NOT_OBSERVED = "supported_but_not_observed"
EVIDENCE_UNSUPPORTED = "unsupported"


def _availability(capabilities: Any, capability: str, observed: bool) -> str:
    if capabilities is None or not getattr(capabilities, capability):
        return EVIDENCE_UNSUPPORTED
    return EVIDENCE_AVAILABLE if observed else EVIDENCE_NOT_OBSERVED


def _availability_reason(state: str, capability: str, source: str) -> str:
    if state == EVIDENCE_NOT_OBSERVED:
        return f"{capability} is supported but no matching Trace evidence was observed"
    if source == "historical_unknown":
        return "historical Artifact has no Adapter Capability snapshot and its Adapter is unknown"
    return f"Adapter does not support {capability} evidence"


def read_trace_events(trace_path: str | Path | None) -> list[dict[str, Any]]:
    """Read usable JSONL records from a Trial Trace."""

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

    capabilities, capability_source = capabilities_for_result(result)
    events = read_trace_events(result.get("trace_path"))
    starts: dict[str, dict[str, Any]] = {}
    ends: dict[str, dict[str, Any]] = {}
    test_runs = 0
    for event in events:
        span_id = event.get("span_id")
        if event.get("kind") == "span_start" and span_type_for(event) == "test":
            test_runs += 1
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
    for call in calls:
        name = call["tool_name"] if isinstance(call["tool_name"], str) else "unknown"
        bucket = by_tool.setdefault(name, {"calls": 0, "ok": 0, "error": 0, "denied": 0})
        bucket["calls"] += 1
    for call in closed:
        name = call["tool_name"] if isinstance(call["tool_name"], str) else "unknown"
        bucket = by_tool[name]
        if call["status"] in {"ok", "error", "denied"}:
            bucket[str(call["status"])] += 1

    fingerprint_calls = [call for call in calls if isinstance(call["fingerprint"], str) and call["fingerprint"].startswith("sha256:")]
    duplicate_fingerprints = sum(count - 1 for count in Counter(call["fingerprint"] for call in fingerprint_calls).values() if count > 1)
    failed_fingerprints: set[str] = set()
    tool_retries = 0
    for call in calls:
        fingerprint = call["fingerprint"]
        if not isinstance(fingerprint, str) or not fingerprint.startswith("sha256:"):
            continue
        if fingerprint in failed_fingerprints:
            tool_retries += 1
        if call["status"] in {"error", "denied"}:
            failed_fingerprints.add(fingerprint)
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

    tool_trace = _availability(capabilities, "tool_trace", bool(calls))
    tool_outcomes = _availability(capabilities, "tool_trace", bool(closed))
    repeated_tools = _availability(capabilities, "tool_semantics", bool(fingerprint_calls))
    duplicate_read_evidence = _availability(capabilities, "tool_semantics", bool(readable))
    edit_before_read_evidence = _availability(capabilities, "tool_semantics", semantic_path_available)
    test_retry_evidence = _availability(capabilities, "test_trace", bool(test_runs))
    evidence_availability = {
        "trace": _availability(capabilities, "trace", bool(events)),
        "tool_trace": tool_trace,
        "tool_outcomes": tool_outcomes,
        "repeated_tool_calls": repeated_tools,
        "duplicate_reads": duplicate_read_evidence,
        "edit_before_read": edit_before_read_evidence,
        "test_retries": test_retry_evidence,
    }
    unavailable = {
        field: _availability_reason(state, capability, capability_source)
        for field, state, capability in (
            ("tool_calls", tool_trace, "tool_trace"),
            ("tool_success_rate", tool_outcomes, "tool_trace"),
            ("repeated_tool_calls", repeated_tools, "tool_semantics"),
            ("duplicate_reads", duplicate_read_evidence, "tool_semantics"),
            ("edit_before_read_count", edit_before_read_evidence, "tool_semantics"),
            ("test_retry_count", test_retry_evidence, "test_trace"),
        )
        if state != EVIDENCE_AVAILABLE
    }

    return {
        "version": 1,
        "adapter_capabilities": capabilities.as_dict() if capabilities else None,
        "capability_source": capability_source,
        "tool_calls": len(calls) if tool_trace == EVIDENCE_AVAILABLE else None,
        "closed_tool_calls": len(closed) if tool_outcomes == EVIDENCE_AVAILABLE else None,
        "tool_success_rate": statuses["ok"] / len(closed) if tool_outcomes == EVIDENCE_AVAILABLE else None,
        "tool_error_rate": statuses["error"] / len(closed) if tool_outcomes == EVIDENCE_AVAILABLE else None,
        "denied_tool_attempts": statuses["denied"] if tool_outcomes == EVIDENCE_AVAILABLE else None,
        "failed_tool_calls": statuses["error"] if tool_outcomes == EVIDENCE_AVAILABLE else None,
        "tool_breakdown": by_tool if tool_trace == EVIDENCE_AVAILABLE else None,
        "repeated_tool_calls": duplicate_fingerprints if repeated_tools == EVIDENCE_AVAILABLE else None,
        "tool_retries": tool_retries if repeated_tools == EVIDENCE_AVAILABLE else None,
        "duplicate_reads": duplicate_reads if duplicate_read_evidence == EVIDENCE_AVAILABLE else None,
        "repeated_tool_call_rate": duplicate_fingerprints / len(fingerprint_calls) if repeated_tools == EVIDENCE_AVAILABLE else None,
        "duplicate_read_rate": duplicate_reads / len(readable) if duplicate_read_evidence == EVIDENCE_AVAILABLE else None,
        "edit_before_read_count": edit_before_read if edit_before_read_evidence == EVIDENCE_AVAILABLE else None,
        "test_retry_count": test_runs - 1 if test_retry_evidence == EVIDENCE_AVAILABLE else None,
        "availability": {
            "tool_outcomes": tool_outcomes == EVIDENCE_AVAILABLE,
            "repeated_tool_calls": repeated_tools == EVIDENCE_AVAILABLE,
            "duplicate_reads": duplicate_read_evidence == EVIDENCE_AVAILABLE,
            "edit_before_read": edit_before_read_evidence == EVIDENCE_AVAILABLE,
            "test_retries": test_retry_evidence == EVIDENCE_AVAILABLE,
        },
        "evidence_availability": evidence_availability,
        "unavailable": unavailable,
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
    def evidence_state(field: str) -> str:
        states = [
            item.get("evidence_availability", {}).get(field)
            for item in trial_behaviors
            if isinstance(item.get("evidence_availability"), dict)
        ]
        if EVIDENCE_AVAILABLE in states:
            return EVIDENCE_AVAILABLE
        if EVIDENCE_NOT_OBSERVED in states:
            return EVIDENCE_NOT_OBSERVED
        return EVIDENCE_UNSUPPORTED

    evidence_availability = {
        field: evidence_state(field)
        for field in ("tool_outcomes", "repeated_tool_calls", "duplicate_reads", "edit_before_read", "test_retries")
    }
    return {
        "trial_count": len(jobs),
        "instrumented_trial_count": count,
        "tool_success_rate": average("tool_success_rate"),
        "tool_error_rate": average("tool_error_rate"),
        "denied_tool_attempts": sum(
            int(item["denied_tool_attempts"])
            for item in trial_behaviors if isinstance(item.get("denied_tool_attempts"), int)
        ),
        "repeated_tool_call_rate": average("repeated_tool_call_rate"),
        "duplicate_read_rate": average("duplicate_read_rate"),
        "edit_before_read_count": sum(int(item["edit_before_read_count"]) for item in trial_behaviors if isinstance(item.get("edit_before_read_count"), int)),
        "verification_coverage": len(covered) / len(verified) if verified else None,
        "availability": {
            field: state == EVIDENCE_AVAILABLE for field, state in evidence_availability.items()
        },
        "evidence_availability": evidence_availability,
        "unavailable": {
            **({} if evidence_availability["test_retries"] == EVIDENCE_AVAILABLE else {
                "test_retry_count": "no Trial emitted available test_retry evidence"
            }),
            **({} if count else {"all_behavior_metrics": "no Trial behavior summaries available"}),
        },
    }
