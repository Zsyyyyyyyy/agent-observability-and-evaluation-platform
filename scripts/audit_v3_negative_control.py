#!/usr/bin/env python3
"""Read-only audit for the V3-derived negative-control experiment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


EXPECTED_CASES, EXPECTED_TRIALS, EXPECTED_REDUNDANT_COMPLETIONS = 8, 3, 2


class AuditError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"{label}: invalid JSON ({exc})") from exc
    require(isinstance(payload, dict), f"{label}: expected object")
    return payload


def selected_results(runtime: Path, arm: str) -> list[tuple[Path, dict[str, Any]]]:
    rows = []
    for case_dir in sorted((runtime / arm).glob("*")):
        for trial_dir in sorted(case_dir.glob("*_trial_*")):
            path = trial_dir / "result.json"
            if path.is_file():
                rows.append((path, load_json(path, f"{arm} result")))
    return rows


def trace_events(path: Path, result: dict[str, Any]) -> list[dict[str, Any]]:
    trace_path = Path(str(result.get("trace_path", "")))
    if not trace_path.is_file():
        trace_path = path.parent / "attempts" / str(result.get("attempt_id", "")) / "trace.jsonl"
    require(trace_path.is_file(), f"{path}: selected trace is missing")
    rows = []
    for number, line in enumerate(trace_path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AuditError(f"{trace_path}:{number}: invalid JSON ({exc})") from exc
        require(isinstance(event, dict), f"{trace_path}:{number}: non-object event")
        rows.append(event)
    return rows


def audit_negative_trace(path: Path, result: dict[str, Any]) -> None:
    trace = trace_events(path, result)
    markers = [item for item in trace if item.get("kind") == "event" and item.get("name") == "negative_control_redundant_call"]
    require(len(markers) == EXPECTED_REDUNDANT_COMPLETIONS, f"{path}: expected two negative-control markers")
    require([(item.get("attributes") or {}).get("ordinal") for item in markers] == [1, 2], f"{path}: marker ordinals are not [1, 2]")
    first_marker = markers[0].get("event_seq")
    require(isinstance(first_marker, int), f"{path}: marker has no event sequence")
    after = [item for item in trace if isinstance(item.get("event_seq"), int) and item["event_seq"] > first_marker]
    model_starts = [item for item in after if item.get("kind") == "span_start" and item.get("name") == "model.call"]
    tool_starts = [item for item in after if item.get("kind") == "span_start" and item.get("name") == "tool.call"]
    require(len(model_starts) == EXPECTED_REDUNDANT_COMPLETIONS, f"{path}: expected two post-terminal model calls")
    require(not tool_starts, f"{path}: post-terminal tool call is forbidden")


def audit(runtime: Path, *, expected_case_count: int = EXPECTED_CASES) -> dict[str, Any]:
    experiment = load_json(runtime / "experiment.json", "experiment")
    protocol = load_json(runtime / "protocol.json", "protocol")
    gate = load_json(runtime / "gate-negative.json", "negative Gate")
    require(protocol.get("comparison_intent") == "v3_runtime_redundant_completion_negative_control", "protocol: wrong intervention intent")
    require(protocol.get("allowed_differences") == ["agents[].runtime_negative_control_post_terminal_completions"], "protocol: unexpected allowed differences")
    require((experiment.get("protocol") or {}).get("comparability", {}).get("level") == "strict", "experiment: protocol is not strict")
    require(experiment.get("baseline_id") == "champion" and experiment.get("candidate_id") == "negative", "experiment: wrong comparison arms")
    require((gate.get("decision") or {}).get("status") == "hold" and gate.get("passed") is False, "negative Gate: expected HOLD")
    cases = set(experiment.get("experiment") or [])
    require(len(cases) == expected_case_count and experiment.get("trial_count_required_per_case") == EXPECTED_TRIALS, "experiment: invalid coverage declaration")
    counts = {}
    for arm, version in (("champion", "external-openai-v3"), ("negative", "external-openai-v3-negative")):
        results = selected_results(runtime, arm)
        expected_trials = expected_case_count * EXPECTED_TRIALS
        require(len(results) == expected_trials, f"{arm}: expected {expected_trials} selected Trials")
        per_case: dict[str, int] = {}
        for path, result in results:
            case_id = str(result.get("case_id") or path.parent.parent.name)
            per_case[case_id] = per_case.get(case_id, 0) + 1
            require(result.get("agent_version") == version, f"{path}: wrong version")
            require(result.get("status") == "completed" and (result.get("evaluation") or {}).get("passed") is True, f"{path}: invalid Trial")
            if arm == "negative":
                require(result.get("agent_profile") == "targeted-context-verify-v3-plus-two-redundant-completions", f"{path}: wrong negative profile")
                audit_negative_trace(path, result)
        require(set(per_case) == cases and all(count == EXPECTED_TRIALS for count in per_case.values()), f"{arm}: incomplete Case coverage")
        counts[arm] = len(results)
    return {"passed": True, "trials": counts, "gate": "HOLD", "redundant_model_calls_per_negative_trial": EXPECTED_REDUNDANT_COMPLETIONS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", default=".runtime/external-openai-v3-negative-control")
    parser.add_argument("--expected-case-count", type=int, default=EXPECTED_CASES)
    args = parser.parse_args()
    try:
        require(args.expected_case_count > 0, "expected case count must be positive")
        print(json.dumps(audit(Path(args.runtime), expected_case_count=args.expected_case_count), ensure_ascii=False, indent=2))
    except AuditError as exc:
        print(f"AUDIT FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
