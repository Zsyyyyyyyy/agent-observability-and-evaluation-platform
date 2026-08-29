"""Trace-backed Trial behavior snapshots and paired version deltas."""

from __future__ import annotations

from statistics import median
from typing import Any

from regression_lab.behavior import read_trace_events, summarize_trial_behavior
from regression_lab.adapters import capabilities_for_result
from regression_lab.schema import span_type_for


SNAPSHOT_SCHEMA_VERSION = 1
_DELTA_FIELDS = (
    "model_calls",
    "tool_calls",
    "tool_success_rate",
    "duplicate_reads",
    "repeated_tool_calls",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "duration_ms",
)
_PATTERN_NAMES = (
    "duplicate_read",
    "repeated_tool_call",
    "tool_retry",
    "failed_tool_call",
    "denied_tool_call",
    "test_retry",
    "post_terminal_call",
)


def _classification(delta: int | float | None, *, higher_is_better: bool = False) -> str:
    if delta is None:
        return "not_available"
    if delta == 0:
        return "unchanged"
    improved = delta > 0 if higher_is_better else delta < 0
    return "improved" if improved else "regressed"


def _usage_and_model_calls(result: dict[str, Any]) -> tuple[int, dict[str, int], bool]:
    starts: dict[str, bool] = {}
    model_calls = 0
    usage_observed = False
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for event in read_trace_events(result.get("trace_path")):
        span_id = event.get("span_id")
        if event.get("kind") == "span_start" and isinstance(span_id, str):
            starts[span_id] = span_type_for(event) == "llm"
            if starts[span_id]:
                model_calls += 1
        elif event.get("kind") == "span_end" and isinstance(span_id, str) and starts.get(span_id):
            attributes = event.get("attributes") if isinstance(event.get("attributes"), dict) else {}
            usage = attributes.get("usage")
            if not isinstance(usage, dict):
                continue
            for key in totals:
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    totals[key] += value
                    usage_observed = True
    if not any(totals.values()):
        usage = result.get("model_usage")
        if isinstance(usage, dict):
            for key in totals:
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    totals[key] = value
                    usage_observed = True
    return model_calls, totals, usage_observed


def _duration_ms(result: dict[str, Any]) -> int | float | None:
    for score in result.get("scores", []):
        if not isinstance(score, dict) or score.get("evaluator") != "budget":
            continue
        value = (score.get("actual") or {}).get("duration_ms")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    value = result.get("duration_ms")
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _semantic_patterns(result: dict[str, Any], tool_behavior: dict[str, Any]) -> dict[str, int | None]:
    events = sorted(
        read_trace_events(result.get("trace_path")),
        key=lambda event: event.get("event_seq") if isinstance(event.get("event_seq"), int) else -1,
    )
    terminal_spans: set[str] = set()
    terminal_complete = False
    test_runs = 0
    post_terminal_calls = 0
    for event in events:
        span_id = event.get("span_id")
        if event.get("kind") == "span_start":
            attributes = event.get("attributes") if isinstance(event.get("attributes"), dict) else {}
            is_terminal = event.get("name") == "agent.finalize" or attributes.get("terminal") is True
            if is_terminal and isinstance(span_id, str):
                terminal_spans.add(span_id)
            span_type = span_type_for(event)
            if span_type == "test":
                test_runs += 1
            elif terminal_complete and span_type in {"llm", "tool", "test"}:
                post_terminal_calls += 1
        elif event.get("kind") == "span_end" and isinstance(span_id, str) and span_id in terminal_spans:
            terminal_complete = True
    def count(name: str) -> int | None:
        value = tool_behavior.get(name)
        return value if isinstance(value, int) else None

    return {
        "duplicate_read": count("duplicate_reads"),
        "repeated_tool_call": count("repeated_tool_calls"),
        "tool_retry": count("tool_retries"),
        "failed_tool_call": count("failed_tool_calls"),
        "denied_tool_call": count("denied_tool_attempts"),
        "test_retry": test_runs - 1 if tool_behavior.get("evidence_availability", {}).get("test_retries") == "available" else None,
        "post_terminal_call": post_terminal_calls,
    }


def _pattern_changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, int | None]:
    before_patterns = before.get("patterns") if isinstance(before.get("patterns"), dict) else {}
    after_patterns = after.get("patterns") if isinstance(after.get("patterns"), dict) else {}
    return {
        pattern: after_patterns[pattern] - before_patterns[pattern]
        if isinstance(after_patterns.get(pattern), int)
        and isinstance(before_patterns.get(pattern), int)
        else None
        for pattern in _PATTERN_NAMES
    }


def _pattern_lists(changes: dict[str, int | None]) -> tuple[list[dict[str, int | str]], list[dict[str, int | str]]]:
    removed = [{"pattern": pattern, "delta": value} for pattern, value in changes.items() if isinstance(value, int) and value < 0]
    added = [{"pattern": pattern, "delta": value} for pattern, value in changes.items() if isinstance(value, int) and value > 0]
    return removed, added


def _case_diffs(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for pair in pairs:
        by_case.setdefault(pair["case_id"], []).append(pair)
    case_diffs = []
    for case_id, case_pairs in sorted(by_case.items()):
        metrics = {}
        for field in _DELTA_FIELDS:
            values = [pair["delta"][field] for pair in case_pairs if isinstance(pair["delta"][field], (int, float))]
            value = median(values) if values else None
            metrics[field] = {
                "median_delta": value,
                "classification": _classification(value, higher_is_better=field == "tool_success_rate"),
                "available_trial_count": len(values),
            }
        patterns = {}
        for pattern in _PATTERN_NAMES:
            values = [pair["pattern_delta"][pattern] for pair in case_pairs if isinstance(pair["pattern_delta"][pattern], int)]
            value = sum(values) if values else None
            patterns[pattern] = {
                "delta": value,
                "classification": _classification(value),
                "available_trial_count": len(values),
            }
        case_diffs.append({
            "case_id": case_id,
            "paired_trial_count": len(case_pairs),
            "metrics": metrics,
            "patterns": patterns,
        })
    return case_diffs


def _summary(case_diffs: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, bool], dict[str, str]]:
    summary = {"metrics": {}, "patterns": {}}
    availability: dict[str, bool] = {}
    unavailable: dict[str, str] = {}
    for field in _DELTA_FIELDS:
        entries = [case["metrics"][field] for case in case_diffs if case["metrics"][field]["classification"] != "not_available"]
        availability[field] = bool(entries)
        if not entries:
            unavailable[field] = "no paired Trial measurements"
            continue
        values = [entry["median_delta"] for entry in entries]
        summary["metrics"][field] = {
            "median_delta": median(values),
            "improved_cases": sum(entry["classification"] == "improved" for entry in entries),
            "unchanged_cases": sum(entry["classification"] == "unchanged" for entry in entries),
            "regressed_cases": sum(entry["classification"] == "regressed" for entry in entries),
            "available_case_count": len(entries),
        }
    for pattern in _PATTERN_NAMES:
        entries = [case["patterns"][pattern] for case in case_diffs if case["patterns"][pattern]["classification"] != "not_available"]
        availability[pattern] = bool(entries)
        if not entries:
            unavailable[pattern] = "no paired Trial pattern measurements"
            continue
        summary["patterns"][pattern] = {
            "improved_cases": sum(entry["classification"] == "improved" for entry in entries),
            "unchanged_cases": sum(entry["classification"] == "unchanged" for entry in entries),
            "regressed_cases": sum(entry["classification"] == "regressed" for entry in entries),
            "available_case_count": len(entries),
        }
    return summary, availability, unavailable


def snapshot_trial_behavior(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize one Trial directly from its Trace and Result Artifact."""

    tool_behavior = summarize_trial_behavior(result)
    capabilities, capability_source = capabilities_for_result(result)
    model_calls, usage, usage_observed = _usage_and_model_calls(result)
    model_call_availability = "available" if capabilities and capabilities.model_usage and model_calls else (
        "supported_but_not_observed" if capabilities and capabilities.model_usage else "unsupported"
    )
    model_usage_availability = "available" if capabilities and capabilities.model_usage and usage_observed else (
        "supported_but_not_observed" if capabilities and capabilities.model_usage else "unsupported"
    )
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "capability_source": capability_source,
        "evidence_provenance": result.get("evidence_provenance") if isinstance(result.get("evidence_provenance"), dict) else {},
        "evidence_availability": {
            "model_calls": model_call_availability,
            "model_usage": model_usage_availability,
            **(tool_behavior.get("evidence_availability") or {}),
        },
        "model_calls": model_calls if model_call_availability == "available" else None,
        "tool_calls": tool_behavior["tool_calls"],
        "tool_breakdown": {
            name: values["calls"] for name, values in (tool_behavior.get("tool_breakdown") or {}).items()
            if isinstance(values, dict) and isinstance(values.get("calls"), int)
        },
        "tool_success_rate": tool_behavior["tool_success_rate"],
        "duplicate_reads": tool_behavior["duplicate_reads"],
        "repeated_tool_calls": tool_behavior["repeated_tool_calls"],
        "input_tokens": usage["prompt_tokens"] if model_usage_availability == "available" else None,
        "output_tokens": usage["completion_tokens"] if model_usage_availability == "available" else None,
        "total_tokens": usage["total_tokens"] if model_usage_availability == "available" else None,
        "duration_ms": _duration_ms(result),
        "patterns": _semantic_patterns(result, tool_behavior),
    }


def behavior_deltas(
    baseline_jobs: list[dict[str, Any]],
    candidate_jobs: list[dict[str, Any]],
    *,
    baseline_version: str | None = None,
    candidate_version: str | None = None,
) -> dict[str, Any]:
    """Pair persisted Trial snapshots by Case and trial index."""

    def indexed(jobs: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
        entries: dict[tuple[str, int], dict[str, Any]] = {}
        for job in jobs:
            case_id, trial_index = job.get("case_id"), job.get("trial_index")
            snapshot = job.get("behavior_snapshot")
            if isinstance(case_id, str) and isinstance(trial_index, int) and isinstance(snapshot, dict):
                entries[(case_id, trial_index)] = snapshot
        return entries

    baseline, candidate = indexed(baseline_jobs), indexed(candidate_jobs)
    pairs = []
    for case_id, trial_index in sorted(set(baseline) & set(candidate)):
        before, after = baseline[(case_id, trial_index)], candidate[(case_id, trial_index)]
        pattern_delta = _pattern_changes(before, after)
        removed_patterns, added_patterns = _pattern_lists(pattern_delta)
        pairs.append({
            "case_id": case_id,
            "trial_index": trial_index,
            "baseline_version": baseline_version,
            "candidate_version": candidate_version,
            "delta": {
                field: after[field] - before[field]
                if isinstance(after.get(field), (int, float)) and not isinstance(after.get(field), bool)
                and isinstance(before.get(field), (int, float)) and not isinstance(before.get(field), bool)
                else None
                for field in _DELTA_FIELDS
            },
            "pattern_delta": pattern_delta,
            "removed_patterns": removed_patterns,
            "added_patterns": added_patterns,
        })
    aggregate_changes = {
        pattern: sum(
            pair["pattern_delta"][pattern]
            for pair in pairs
            if isinstance(pair["pattern_delta"][pattern], int)
        )
        for pattern in _PATTERN_NAMES
    }
    removed_patterns, added_patterns = _pattern_lists(aggregate_changes)
    case_diffs = _case_diffs(pairs)
    summary, availability, unavailable = _summary(case_diffs)
    availability["behavior_diff"] = bool(pairs)
    if not pairs:
        unavailable["behavior_diff"] = "no paired Trial Behavior Snapshots"
    return {
        "version": SNAPSHOT_SCHEMA_VERSION,
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "diagnostic_only": True,
        "paired_trial_count": len(pairs),
        "unpaired_baseline_trial_count": len(set(baseline) - set(candidate)),
        "unpaired_candidate_trial_count": len(set(candidate) - set(baseline)),
        "deltas": pairs,
        "case_diffs": case_diffs,
        "summary": summary,
        "availability": availability,
        "unavailable": unavailable,
        "removed_patterns": removed_patterns,
        "added_patterns": added_patterns,
    }
