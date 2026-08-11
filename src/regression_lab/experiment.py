"""Experiment expansion and deterministic Baseline/Candidate comparison."""

from __future__ import annotations

from statistics import mean
from typing import Any, Iterable


def expand_experiment(jobs: Iterable[dict[str, Any]], agents: Iterable[dict[str, str]]) -> list[dict[str, Any]]:
    expanded = []
    for agent in agents:
        for job in jobs:
            expanded.append({
                **job,
                "agent_id": agent["id"],
                "agent_version": agent["version"],
                "experiment_job_id": f"{agent['id']}__{job['job_id']}",
            })
    return expanded


def _metrics(summary: dict[str, Any]) -> dict[str, float]:
    jobs = summary.get("jobs", [])
    count = len(jobs) or 1
    completed = sum(job.get("status") == "completed" for job in jobs)
    evaluated = sum(bool(job.get("evaluation_passed")) for job in jobs)
    tests = sum(bool(job.get("test_passed")) for job in jobs)
    model_failed = sum(job.get("status") == "model_failed" for job in jobs)
    trace_incomplete = sum(job.get("status") == "trace_incomplete" for job in jobs)
    infra_failed = sum(job.get("status") == "infra_failed" for job in jobs)
    return {
        "trial_count": float(len(jobs)),
        "completion_rate": completed / count,
        "evaluation_pass_rate": evaluated / count,
        "test_pass_rate": tests / count,
        "model_failed_rate": model_failed / count,
        "trace_incomplete_rate": trace_incomplete / count,
        "infra_failed_rate": infra_failed / count,
        "avg_tool_calls": mean([job.get("tool_calls", 0) for job in jobs]) if jobs else 0.0,
        "avg_duration_ms": mean([job.get("duration_ms", 0) for job in jobs]) if jobs else 0.0,
        "avg_added_lines": mean([job.get("added_lines", 0) for job in jobs]) if jobs else 0.0,
        "avg_deleted_lines": mean([job.get("deleted_lines", 0) for job in jobs]) if jobs else 0.0,
        "avg_model_tokens": mean([job.get("model_tokens", 0) for job in jobs]) if jobs else 0.0,
    }


def compare_summaries(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    baseline_metrics = _metrics(baseline)
    candidate_metrics = _metrics(candidate)
    deltas = {
        key: candidate_metrics[key] - baseline_metrics[key]
        for key in baseline_metrics
        if key != "trial_count"
    }
    classifications: dict[str, list[str]] = {"improved": [], "regressed": [], "unchanged": []}
    for key in ("completion_rate", "evaluation_pass_rate", "test_pass_rate"):
        delta = deltas[key]
        bucket = "improved" if delta > 0 else "regressed" if delta < 0 else "unchanged"
        classifications[bucket].append(key)
    for key in (
        "avg_tool_calls", "avg_duration_ms", "avg_model_tokens",
        "model_failed_rate", "trace_incomplete_rate", "infra_failed_rate",
    ):
        delta = deltas[key]
        bucket = "improved" if delta < 0 else "regressed" if delta > 0 else "unchanged"
        classifications[bucket].append(key)
    return {
        "baseline": baseline_metrics,
        "candidate": candidate_metrics,
        "delta": deltas,
        "classification": classifications,
    }
