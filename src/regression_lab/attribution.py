"""Deterministic primary-failure attribution for Trial diagnostics.

Attribution is deliberately a diagnostic layer.  It never changes a Trial's
status, evaluator scores, or promotion evidence; it only makes the reason for
an already non-passing Trial explicit and mutually exclusive.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


FAILURE_KINDS = ("passed", "model", "infrastructure", "evidence", "policy", "agent")


def _scores(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["evaluator"]: item
        for item in result.get("scores", [])
        if isinstance(item, dict) and isinstance(item.get("evaluator"), str)
    }


def attribute_trial(result: dict[str, Any]) -> dict[str, str]:
    """Return one primary cause, with a stable precedence for mixed failures."""

    status = result.get("status")
    scores = _scores(result)
    if status == "completed" and (result.get("evaluation") or {}).get("passed") is True:
        return {"kind": "passed", "reason": "valid_platform_evidence"}
    if status == "model_failed":
        return {"kind": "model", "reason": "model_provider_or_response_failure"}
    if status in {"infra_failed", "timed_out"}:
        return {"kind": "infrastructure", "reason": "runner_sandbox_or_deadline_failure"}
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
