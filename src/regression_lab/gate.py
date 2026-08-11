"""Deterministic release gate for Agent experiment comparison reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GateRule:
    name: str
    actual: float
    expected: str
    passed: bool
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "actual": self.actual,
            "expected": self.expected,
            "passed": self.passed,
            "message": self.message,
        }


def _number(policy: dict[str, Any], key: str, default: float) -> float:
    value = policy.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"policy.{key} must be numeric")
    return float(value)


def evaluate_gate(experiment: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Evaluate candidate promotion without re-running Agents.

    Reliability and correctness are hard fail-closed checks. Efficiency metrics
    are bounded regressions: a policy can tolerate small cost movement while
    blocking material regressions.
    """

    comparison = experiment.get("comparison")
    if not isinstance(comparison, dict) or not isinstance(comparison.get("baseline"), dict) or not isinstance(comparison.get("candidate"), dict):
        raise ValueError("experiment.comparison with baseline and candidate metrics is required")
    baseline, candidate = comparison["baseline"], comparison["candidate"]
    rules: list[GateRule] = []

    def lower_bound(metric: str, policy_key: str, default: float, label: str) -> None:
        threshold = _number(policy, policy_key, default)
        delta = float(candidate.get(metric, 0)) - float(baseline.get(metric, 0))
        rules.append(GateRule(label, delta, f">= {threshold}", delta >= threshold, f"candidate - baseline {metric}"))

    def upper_bound(metric: str, policy_key: str, default: float, label: str) -> None:
        threshold = _number(policy, policy_key, default)
        delta = float(candidate.get(metric, 0)) - float(baseline.get(metric, 0))
        rules.append(GateRule(label, delta, f"<= {threshold}", delta <= threshold, f"candidate - baseline {metric}"))

    lower_bound("completion_rate", "min_completion_rate_delta", 0.0, "completion_rate_non_regression")
    lower_bound("evaluation_pass_rate", "min_evaluation_pass_rate_delta", 0.0, "evaluation_pass_rate_non_regression")
    upper_bound("model_failed_rate", "max_model_failed_rate_delta", 0.0, "model_failed_rate_non_regression")
    upper_bound("trace_incomplete_rate", "max_trace_incomplete_rate", 0.0, "trace_incomplete_rate_limit")
    upper_bound("infra_failed_rate", "max_infra_failed_rate", 0.0, "infra_failed_rate_limit")
    upper_bound("avg_duration_ms", "max_avg_duration_ms_delta", 0.0, "average_duration_non_regression")

    baseline_tools = float(baseline.get("avg_tool_calls", 0))
    candidate_tools = float(candidate.get("avg_tool_calls", 0))
    tool_ratio = (candidate_tools - baseline_tools) / baseline_tools if baseline_tools else 0.0
    tool_threshold = _number(policy, "max_avg_tool_calls_ratio", 0.10)
    rules.append(GateRule("average_tool_calls_limit", tool_ratio, f"<= {tool_threshold}", tool_ratio <= tool_threshold, "relative candidate tool-call increase"))

    baseline_tokens = float(baseline.get("avg_model_tokens", 0))
    candidate_tokens = float(candidate.get("avg_model_tokens", 0))
    token_ratio = (candidate_tokens - baseline_tokens) / baseline_tokens if baseline_tokens else 0.0
    token_threshold = _number(policy, "max_avg_model_tokens_ratio", 0.10)
    rules.append(GateRule("average_model_tokens_limit", token_ratio, f"<= {token_threshold}", token_ratio <= token_threshold, "relative candidate token increase"))

    return {
        "schema_version": 1,
        "baseline_id": experiment.get("baseline_id"),
        "candidate_id": experiment.get("candidate_id"),
        "passed": all(rule.passed for rule in rules),
        "rules": [rule.as_dict() for rule in rules],
        "evidence": {"comparison": comparison},
    }
