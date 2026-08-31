"""Deterministic primary-failure attribution for Trial diagnostics.

Attribution is deliberately a diagnostic layer.  It never changes a Trial's
status, evaluator scores, or promotion evidence; it only makes the reason for
an already non-passing Trial explicit and mutually exclusive.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from regression_lab.behavior import read_trace_events
from regression_lab.schema import span_type_for


FAILURE_KINDS = ("passed", "model", "infrastructure", "evidence", "policy", "agent")


def _scores(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["evaluator"]: item
        for item in result.get("scores", [])
        if isinstance(item, dict) and isinstance(item.get("evaluator"), str)
    }


def _primary_attribution(result: dict[str, Any]) -> dict[str, str]:
    """Return one primary cause, with a stable precedence for mixed failures."""

    status = result.get("status")
    scores = _scores(result)
    if status == "completed" and (result.get("evaluation") or {}).get("passed") is True:
        return {"kind": "passed", "reason": "valid_platform_evidence"}
    if status == "model_failed":
        return {"kind": "model", "reason": "model_provider_or_response_failure"}
    if status in {"infra_failed", "timed_out"}:
        return {"kind": "infrastructure", "reason": "runner_sandbox_or_deadline_failure"}
    if status == "environment_mismatch":
        return {"kind": "evidence", "reason": "runtime_environment_does_not_match_protocol"}
    if status == "trace_incomplete" or scores.get("trace_completeness", {}).get("passed") is False:
        return {"kind": "evidence", "reason": "trace_or_evidence_validation_failed"}
    for evaluator in ("path_policy", "diff", "tool_integrity"):
        if scores.get(evaluator, {}).get("passed") is False:
            return {"kind": "policy", "reason": f"{evaluator}_violation"}
    if scores.get("budget", {}).get("passed") is False:
        return {"kind": "agent", "reason": "agent_budget_exceeded"}
    if scores.get("test", {}).get("passed") is False:
        return {"kind": "agent", "reason": "task_test_failed_or_not_run"}
    return {"kind": "agent", "reason": "agent_execution_did_not_produce_valid_pass"}


def _trace_spans(result: dict[str, Any]) -> list[dict[str, Any]]:
    starts: dict[str, dict[str, Any]] = {}
    ends: dict[str, dict[str, Any]] = {}
    for event in read_trace_events(result.get("trace_path")):
        span_id = event.get("span_id")
        if not isinstance(span_id, str):
            continue
        if event.get("kind") == "span_start":
            starts[span_id] = event
        elif event.get("kind") == "span_end":
            ends[span_id] = event
    spans = []
    for span_id, start in starts.items():
        attrs = start.get("attributes") if isinstance(start.get("attributes"), dict) else {}
        spans.append({
            "span_id": span_id,
            "span_type": span_type_for(start),
            "name": start.get("name"),
            "tool_name": attrs.get("tool_name"),
            "target_path": attrs.get("target_path"),
            "status": (ends.get(span_id) or {}).get("status"),
            "sequence": start.get("event_seq"),
        })
    return sorted(spans, key=lambda span: span["sequence"] if isinstance(span["sequence"], int) else -1)


def _failure_span(span: dict[str, Any]) -> dict[str, str]:
    values = {key: span[key] for key in ("span_id", "span_type", "name") if isinstance(span.get(key), str)}
    if isinstance(span.get("tool_name"), str):
        values["tool_name"] = span["tool_name"]
    return values


def _last(spans: list[dict[str, Any]], predicate: Any) -> dict[str, Any] | None:
    return next((span for span in reversed(spans) if predicate(span)), None)


def attribute_failure_span(result: dict[str, Any], attribution: dict[str, str] | None = None) -> dict[str, Any]:
    """Locate one failure Span from evaluator evidence without inferring intent."""

    attribution = attribution or _primary_attribution(result)
    scores = _scores(result)
    spans = _trace_spans(result)
    reason = attribution["reason"]
    if attribution["kind"] == "model":
        span = _last(spans, lambda item: item["span_type"] == "llm" and item["status"] == "error")
        return {
            "failure_span": _failure_span(span) if span else None,
            "evidence": {"status": "model_failed"},
        }
    if reason == "task_test_failed_or_not_run":
        span = _last(spans, lambda item: item["span_type"] == "test")
        return {
            "failure_span": _failure_span(span) if span else None,
            "evidence": {"evaluator": "test"},
        }
    if attribution["kind"] != "policy":
        return {"failure_span": None, "evidence": {}}

    evaluator = reason.removesuffix("_violation")
    score = scores.get(evaluator, {})
    target_paths: list[str] = []
    if evaluator == "path_policy":
        target_paths = [path for path in score.get("evidence", {}).get("violating_files", []) if isinstance(path, str)]
    elif evaluator == "diff":
        target_paths = [path for path in score.get("evidence", {}).get("changed_files", []) if isinstance(path, str)]
    elif evaluator == "tool_integrity":
        actual = score.get("actual") or {}
        tool_names = set(actual.get("unauthorized", [])) | set(actual.get("denied_attempts", []))
        span = _last(spans, lambda item: item["span_type"] == "tool" and item["tool_name"] in tool_names)
        return {
            "failure_span": _failure_span(span) if span else None,
            "evidence": {"evaluator": evaluator},
        }
    span = _last(spans, lambda item: item["span_type"] == "tool" and item["target_path"] in target_paths)
    evidence: dict[str, Any] = {"evaluator": evaluator}
    if span and isinstance(span.get("target_path"), str):
        evidence["target_path"] = span["target_path"]
    return {"failure_span": _failure_span(span) if span else None, "evidence": evidence}


def attribute_trial(result: dict[str, Any]) -> dict[str, Any]:
    """Return one primary cause plus deterministic Trace localization."""

    attribution = _primary_attribution(result)
    return {**attribution, **attribute_failure_span(result, attribution)}


def aggregate_attribution(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Build raw and conditionally eligible reliability views for a version."""

    attributions = [job.get("failure_attribution") for job in jobs if isinstance(job.get("failure_attribution"), dict)]
    counts = Counter(
        item.get("kind") for item in attributions
        if item.get("kind") in FAILURE_KINDS
    )
    total = len(jobs)
    raw_passes = counts["passed"]
    eligible = total - counts["model"] - counts["infrastructure"]
    agent_quality_passes = raw_passes
    reasons = Counter(
        item.get("reason") for item in attributions
        if isinstance(item.get("reason"), str) and item.get("kind") != "passed"
    )
    return {
        "trial_count": total,
        "attributed_trial_count": len(attributions),
        "raw_reliability": {
            "valid_pass_rate": raw_passes / total if total else None,
            "valid_pass_count": raw_passes,
            "failure_count": total - raw_passes,
            "includes": ["agent", "model", "infrastructure", "evidence", "policy"],
        },
        "agent_quality": {
            "eligible_trial_count": eligible,
            "excluded_external_failure_count": counts["model"] + counts["infrastructure"],
            "valid_pass_rate": agent_quality_passes / eligible if eligible else None,
            "valid_pass_count": agent_quality_passes,
            "includes": ["agent", "evidence", "policy"],
            "excludes": ["model", "infrastructure"],
        },
        "counts": {kind: counts[kind] for kind in FAILURE_KINDS},
        "reasons": dict(sorted(reasons.items())),
        "unavailable": ({} if len(attributions) == total else {"failure_attribution": "some historical summaries could not be attributed"}),
    }
