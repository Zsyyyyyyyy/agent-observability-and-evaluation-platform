#!/usr/bin/env python3
"""Audit the offline evidence for the V3 -> V4.1 confirmation benchmark.

This command deliberately reads existing artifacts only.  It does not call a
model, rebuild an experiment, or mutate the artifact directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


EXPECTED_BASELINE = "external-openai-v3"
EXPECTED_CANDIDATE = "external-openai-v4.1"
EXPECTED_CANDIDATE_PROFILE = "bounded-success-stop-verify-v4-1"
EXPECTED_TRIALS_PER_CASE = 3
EXPECTED_CASE_COUNT = 8
EXPECTED_STOP_REASON = "verification_passed_policy"


class AuditError(ValueError):
    """Raised when an artifact fails an audit invariant."""


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"{label}: cannot read valid JSON ({exc})") from exc
    if not isinstance(value, dict):
        raise AuditError(f"{label}: expected a JSON object")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def selected_results(root: Path, label: str) -> list[tuple[Path, dict[str, Any]]]:
    values: list[tuple[Path, dict[str, Any]]] = []
    for case_dir in sorted(root.glob("*") if root.exists() else []):
        if not case_dir.is_dir():
            continue
        for trial_dir in sorted(case_dir.glob("*_trial_*")):
            result_path = trial_dir / "result.json"
            if not result_path.is_file():
                continue
            values.append((result_path, load_json(result_path, f"{label} result")))
    return values


def trace_path_for(result_path: Path, result: dict[str, Any]) -> Path:
    raw = result.get("trace_path")
    require(isinstance(raw, str) and raw, f"{result_path}: missing trace_path")
    path = Path(raw)
    if path.is_file():
        return path
    # Absolute paths in local artifacts are not portable.  Fall back to the
    # selected attempt next to the result when the artifact was copied.
    fallback = result_path.parent / "attempts" / str(result.get("attempt_id", "")) / "trace.jsonl"
    require(fallback.is_file(), f"{result_path}: trace file not found")
    return fallback


def audit_trace(result_path: Path, result: dict[str, Any]) -> None:
    trace_path = trace_path_for(result_path, result)
    events: list[dict[str, Any]] = []
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AuditError(f"{trace_path}: cannot read trace ({exc})") from exc
    for line_number, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AuditError(f"{trace_path}:{line_number}: invalid JSON ({exc})") from exc
        require(isinstance(event, dict), f"{trace_path}:{line_number}: event is not an object")
        events.append(event)

    stops = [
        event
        for event in events
        if event.get("kind") == "event"
        and event.get("name") == "agent.stop"
        and isinstance(event.get("attributes"), dict)
        and event["attributes"].get("reason") == EXPECTED_STOP_REASON
    ]
    require(len(stops) == 1, f"{result_path}: expected one {EXPECTED_STOP_REASON} stop event")
    stop_seq = stops[0].get("event_seq")
    require(isinstance(stop_seq, int), f"{result_path}: stop event has no integer event_seq")
    post_stop_calls = [
        event
        for event in events
        if isinstance(event.get("event_seq"), int)
        and event["event_seq"] > stop_seq
        and event.get("kind") == "span_start"
        and event.get("name") in {"model.call", "tool.call"}
    ]
    require(not post_stop_calls, f"{result_path}: model/tool span started after policy stop")


def audit_results(runtime: Path, experiment: dict[str, Any], *, expected_case_count: int) -> dict[str, Any]:
    cases = experiment.get("experiment")
    require(isinstance(cases, list) and all(isinstance(case, str) for case in cases), "experiment: invalid case list")
    case_ids = set(cases)
    require(len(case_ids) == expected_case_count, f"experiment: expected {expected_case_count} Cases, got {len(case_ids)}")
    require(experiment.get("trial_count_required_per_case") == EXPECTED_TRIALS_PER_CASE, "experiment: trial count is not 3")

    agents = {agent.get("id"): agent.get("version") for agent in experiment.get("agents", []) if isinstance(agent, dict)}
    require(agents.get(experiment.get("baseline_id")) == EXPECTED_BASELINE, "experiment: unexpected baseline version")
    require(agents.get(experiment.get("candidate_id")) == EXPECTED_CANDIDATE, "experiment: unexpected candidate version")

    results_by_label: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for label, directory in (("baseline", runtime / "baseline"), ("candidate", runtime / "candidate")):
        results = selected_results(directory, label)
        expected_trials = expected_case_count * EXPECTED_TRIALS_PER_CASE
        require(len(results) == expected_trials, f"{label}: expected {expected_trials} selected Trials, got {len(results)}")
        counts: dict[str, int] = {}
        for result_path, result in results:
            case_id = result.get("case_id") or result_path.parent.parent.name
            counts[str(case_id)] = counts.get(str(case_id), 0) + 1
            require(str(case_id) in case_ids, f"{label}: unknown Case {case_id}")
            require(result.get("status") == "completed", f"{result_path}: status is not completed")
            require(result.get("evaluation", {}).get("passed") is True, f"{result_path}: evaluation did not pass")
            require(result.get("trace_validation", {}).get("valid") is True, f"{result_path}: trace is not valid")
            require(result.get("test_exit_code") == 0, f"{result_path}: verification command did not exit 0")
            require(result.get("model_failure") is None, f"{result_path}: model failure present")
            require(result.get("error") is None, f"{result_path}: trial error present")
            if label == "candidate":
                require(result.get("agent_version") == EXPECTED_CANDIDATE, f"{result_path}: unexpected candidate version")
                require(result.get("agent_profile") == EXPECTED_CANDIDATE_PROFILE, f"{result_path}: unexpected V4.1 profile")
                require(result.get("agent_exit_reason") == "verification_passed", f"{result_path}: unexpected exit reason")
                audit_trace(result_path, result)
        require(set(counts) == case_ids and all(count == EXPECTED_TRIALS_PER_CASE for count in counts.values()), f"{label}: Case coverage/count mismatch: {counts}")
        results_by_label[label] = results
    return {label: len(results) for label, results in results_by_label.items()}


def audit(runtime: Path, *, expected_case_count: int = EXPECTED_CASE_COUNT) -> dict[str, Any]:
    experiment = load_json(runtime / "experiment.json", "experiment")
    protocol = load_json(runtime / "protocol.json", "protocol")
    gate = load_json(runtime / "gate-report.json", "gate report")

    comparability = experiment.get("protocol", {}).get("comparability", {})
    require(comparability.get("level") == "strict", "experiment: protocol comparability is not strict")
    require(comparability.get("differences") == [], "experiment: protocol contains uncontrolled differences")
    require(protocol.get("schema_version") == 2, "protocol: expected schema_version 2")
    require(gate.get("passed") is True, "gate: promotion gate did not pass")
    require(gate.get("decision", {}).get("status") == "promote", "gate: decision is not promote")
    rules = gate.get("rules")
    require(isinstance(rules, list) and rules and all(rule.get("passed") is True for rule in rules if isinstance(rule, dict)), "gate: one or more rules failed")

    statistics = experiment.get("statistics", {})
    require(statistics.get("method") == "clustered_case_bootstrap", "statistics: expected clustered_case_bootstrap")
    require(statistics.get("eligible_case_count") == expected_case_count, "statistics: incomplete eligible Case coverage")
    metrics = statistics.get("metrics", {})
    required_metrics = {"duration_ms", "model_tokens", "tool_calls"}
    require(required_metrics <= set(metrics), "statistics: missing efficiency metrics")
    for metric_name in required_metrics:
        metric = metrics[metric_name]
        require(metric.get("available") is True, f"statistics: {metric_name} is unavailable")
        require(isinstance(metric.get("ci95"), dict), f"statistics: {metric_name} has no ci95")

    counts = audit_results(runtime, experiment, expected_case_count=expected_case_count)
    return {
        "passed": True,
        "runtime": str(runtime),
        "gate": gate["decision"]["status"].upper(),
        "protocol": "strict",
        "case_count": expected_case_count,
        "trials": counts,
        "candidate_policy_stop_traces": counts["candidate"],
        "post_stop_model_or_tool_spans": 0,
        "statistics": {name: metrics[name]["ci95"] for name in sorted(required_metrics)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", default=".runtime/external-openai-v3-v4-1-benchmark", help="benchmark artifact directory")
    parser.add_argument("--expected-case-count", type=int, default=EXPECTED_CASE_COUNT)
    args = parser.parse_args()
    try:
        require(args.expected_case_count > 0, "expected case count must be positive")
        result = audit(Path(args.runtime), expected_case_count=args.expected_case_count)
    except AuditError as exc:
        print(f"AUDIT FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
