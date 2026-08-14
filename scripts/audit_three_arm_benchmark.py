#!/usr/bin/env python3
"""Read-only audit for the formal V3 / V4.1 / negative-control benchmark.

The audit intentionally never invokes a model or rebuilds an experiment.  It
checks the persisted Protocol, selected Trial evidence, Trace invariant, and
the two independently evaluated Gate reports.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


EXPECTED_CASES = 8
EXPECTED_TRIALS = 3
ARMS = {
    "champion": {"version": "external-openai-v3"},
    "positive": {"version": "external-openai-v4.1", "profile": "bounded-success-stop-verify-v4-1"},
    "negative": {"version": "external-openai-v4-negative", "profile": "negative-control-redundant-call-v4"},
}


class AuditError(ValueError):
    """Raised when evidence cannot support the formal conclusion."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"{label}: cannot read valid JSON ({exc})") from exc
    require(isinstance(value, dict), f"{label}: expected a JSON object")
    return value


def selected_results(runtime: Path, arm: str) -> list[tuple[Path, dict[str, Any]]]:
    results: list[tuple[Path, dict[str, Any]]] = []
    for case_dir in sorted((runtime / arm).glob("*")):
        if not case_dir.is_dir():
            continue
        for trial_dir in sorted(case_dir.glob("*_trial_*")):
            result_path = trial_dir / "result.json"
            if result_path.is_file():
                results.append((result_path, load_json(result_path, f"{arm} result")))
    return results


def trace_path(result_path: Path, result: dict[str, Any]) -> Path:
    raw = result.get("trace_path")
    candidate = Path(raw) if isinstance(raw, str) and raw else None
    if candidate and candidate.is_file():
        return candidate
    fallback = result_path.parent / "attempts" / str(result.get("attempt_id", "")) / "trace.jsonl"
    require(fallback.is_file(), f"{result_path}: selected trace is missing")
    return fallback


def trace_events(result_path: Path, result: dict[str, Any]) -> list[dict[str, Any]]:
    path = trace_path(result_path, result)
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AuditError(f"{path}: cannot read trace ({exc})") from exc
    for line_number, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AuditError(f"{path}:{line_number}: invalid JSON ({exc})") from exc
        require(isinstance(event, dict), f"{path}:{line_number}: event is not an object")
        events.append(event)
    return events


def audit_positive_trace(result_path: Path, result: dict[str, Any]) -> None:
    events = trace_events(result_path, result)
    stops = [event for event in events if event.get("kind") == "event" and event.get("name") == "agent.stop"
             and (event.get("attributes") or {}).get("reason") == "verification_passed_policy"]
    require(len(stops) == 1, f"{result_path}: expected exactly one V4.1 policy stop")
    stop_seq = stops[0].get("event_seq")
    require(isinstance(stop_seq, int), f"{result_path}: policy stop has no integer event_seq")
    post_stop_calls = [event for event in events if isinstance(event.get("event_seq"), int) and event["event_seq"] > stop_seq
                       and event.get("kind") == "span_start" and event.get("name") in {"model.call", "tool.call"}]
    require(not post_stop_calls, f"{result_path}: model/tool span started after V4.1 policy stop")


def audit_negative_trace(result_path: Path, result: dict[str, Any]) -> None:
    events = trace_events(result_path, result)
    markers = [event for event in events if event.get("kind") == "event" and event.get("name") == "negative_control_redundant_call"]
    require(len(markers) == 1, f"{result_path}: expected exactly one negative-control marker")
    marker_seq = markers[0].get("event_seq")
    require(isinstance(marker_seq, int), f"{result_path}: negative-control marker has no integer event_seq")
    after = [event for event in events if isinstance(event.get("event_seq"), int) and event["event_seq"] > marker_seq]
    model_calls = [event for event in after if event.get("kind") == "span_start" and event.get("name") == "model.call"]
    tool_calls = [event for event in after if event.get("kind") == "span_start" and event.get("name") == "tool.call"]
    require(len(model_calls) == 1, f"{result_path}: expected one post-success redundant model call")
    require(not tool_calls, f"{result_path}: negative control executed a tool after its redundant call")


def audit_trials(runtime: Path, experiment: dict[str, Any]) -> dict[str, int]:
    cases = experiment.get("experiment")
    require(isinstance(cases, list) and all(isinstance(case, str) for case in cases), "experiment: invalid Case list")
    case_ids = set(cases)
    require(len(case_ids) == EXPECTED_CASES, f"experiment: expected {EXPECTED_CASES} Cases, got {len(case_ids)}")
    require(experiment.get("trial_count_required_per_case") == EXPECTED_TRIALS, "experiment: expected three Trials per Case")
    counts: dict[str, int] = {}
    for arm, expectation in ARMS.items():
        results = selected_results(runtime, arm)
        require(len(results) == EXPECTED_CASES * EXPECTED_TRIALS, f"{arm}: expected 24 selected Trials, got {len(results)}")
        per_case: dict[str, int] = {}
        for path, result in results:
            case_id = str(result.get("case_id") or path.parent.parent.name)
            per_case[case_id] = per_case.get(case_id, 0) + 1
            require(result.get("agent_version") == expectation["version"], f"{path}: unexpected Agent version")
            require(result.get("status") == "completed", f"{path}: status is not completed")
            require((result.get("evaluation") or {}).get("passed") is True, f"{path}: evaluation did not pass")
            require((result.get("trace_validation") or {}).get("valid") is True, f"{path}: trace is invalid")
            require(result.get("test_exit_code") == 0, f"{path}: verification did not exit 0")
            require(result.get("model_failure") is None and result.get("error") is None, f"{path}: failure evidence present")
            if arm == "positive":
                require(result.get("agent_profile") == expectation["profile"], f"{path}: unexpected positive profile")
                audit_positive_trace(path, result)
            if arm == "negative":
                require(result.get("agent_profile") == expectation["profile"], f"{path}: unexpected negative profile")
                audit_negative_trace(path, result)
        require(set(per_case) == case_ids and all(count == EXPECTED_TRIALS for count in per_case.values()), f"{arm}: incomplete Case coverage")
        counts[arm] = len(results)
    return counts


def audit_gate(gate: dict[str, Any], *, candidate: str, expected_status: str) -> None:
    require(gate.get("candidate_id") == candidate, f"{candidate} Gate: wrong candidate ID")
    status = (gate.get("decision") or {}).get("status")
    require(status == expected_status, f"{candidate} Gate: expected {expected_status}, got {status}")
    require(gate.get("passed") is (expected_status == "promote"), f"{candidate} Gate: passed flag disagrees with decision")
    if expected_status == "promote":
        rules = gate.get("rules")
        require(isinstance(rules, list) and rules and all(isinstance(rule, dict) and rule.get("passed") is True for rule in rules), "positive Gate: a rule did not pass")
    else:
        failures = (gate.get("decision") or {}).get("hard_blocking_failures")
        require(isinstance(failures, list) and failures, "negative Gate: expected at least one blocking rule")


def audit(runtime: Path) -> dict[str, Any]:
    experiment = load_json(runtime / "experiment.json", "experiment")
    protocol = load_json(runtime / "protocol.json", "protocol")
    positive_gate = load_json(runtime / "gate-positive.json", "positive Gate")
    negative_gate = load_json(runtime / "gate-negative.json", "negative Gate")
    require((experiment.get("protocol") or {}).get("comparability", {}).get("level") == "strict", "experiment: protocol is not strictly comparable")
    require(protocol.get("comparison_intent") == "runtime_success_stop_positive_and_negative_control", "protocol: unexpected comparison intent")
    allowed = set(protocol.get("allowed_differences") or [])
    require({"agents[].prompt_profile", "agents[].runtime_success_stop_policy"} <= allowed, "protocol: intervention differences were not declared")
    require(experiment.get("champion_id") == "champion", "experiment: Champion is not declared")
    arms = experiment.get("comparison_arms")
    require(isinstance(arms, dict) and set(arms) == {"positive", "negative"}, "experiment: expected positive and negative comparison arms")
    require(arms["positive"].get("baseline_id") == "champion" and arms["positive"].get("candidate_id") == "positive", "experiment: invalid positive arm")
    require(arms["negative"].get("baseline_id") == "champion" and arms["negative"].get("candidate_id") == "negative", "experiment: invalid negative arm")
    audit_gate(positive_gate, candidate="positive", expected_status="promote")
    audit_gate(negative_gate, candidate="negative", expected_status="hold")
    counts = audit_trials(runtime, experiment)
    return {"passed": True, "runtime": str(runtime), "protocol": "strict", "trials": counts,
            "gates": {"positive": "PROMOTE", "negative": "HOLD"}, "coverage": f"{EXPECTED_CASES}/{EXPECTED_CASES} Cases"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", default=".runtime/external-openai-v3-v4-1-negative-benchmark")
    args = parser.parse_args()
    try:
        result = audit(Path(args.runtime))
    except AuditError as exc:
        print(f"AUDIT FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
