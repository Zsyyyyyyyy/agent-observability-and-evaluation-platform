"""Deterministic release gate for Agent experiment comparison reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GateRule:
    name: str
    actual: float | None
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


def _optional_delta(before: Any, after: Any) -> float | None:
    """Return a numeric candidate-baseline delta when both values exist."""

    if isinstance(before, (int, float)) and not isinstance(before, bool) and isinstance(after, (int, float)) and not isinstance(after, bool):
        return float(after) - float(before)
    return None


def _metric(value: Any) -> float | None:
    """Return a finite Gate metric, never treating absent evidence as zero."""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _unavailable_rule(name: str, expected: str, message: str) -> GateRule:
    return GateRule(name, None, expected, False, message)


def _cost_increase_rule(*, name: str, baseline: float, candidate: float,
                        ratio_policy_key: str, absolute_zero_baseline_policy_key: str,
                        policy: dict[str, Any], label: str) -> GateRule:
    """Compare cost safely when a zero baseline makes a ratio undefined."""

    if baseline == 0:
        increase = candidate
        threshold = _number(policy, absolute_zero_baseline_policy_key, 0.0)
        return GateRule(name, increase, f"<= {threshold} absolute increase (zero baseline)", increase <= threshold,
                        f"candidate {label} when baseline is zero; ratio is undefined")
    ratio = (candidate - baseline) / baseline
    threshold = _number(policy, ratio_policy_key, 0.10)
    return GateRule(name, ratio, f"<= {threshold} relative increase", ratio <= threshold,
                    f"relative candidate {label} increase")


def _absolute_lower_rule(*, name: str, value: Any, policy: dict[str, Any], key: str,
                         default: float, label: str) -> GateRule:
    threshold = _number(policy, key, default)
    actual = _metric(value)
    if actual is None:
        return _unavailable_rule(name, f">= {threshold}", f"candidate {label} is not available")
    return GateRule(name, actual, f">= {threshold}", actual >= threshold, f"candidate {label} absolute minimum")


def _absolute_upper_rule(*, name: str, value: Any, policy: dict[str, Any], key: str,
                         default: float, label: str) -> GateRule:
    threshold = _number(policy, key, default)
    actual = _metric(value)
    if actual is None:
        return _unavailable_rule(name, f"<= {threshold}", f"candidate {label} is not available")
    return GateRule(name, actual, f"<= {threshold}", actual <= threshold, f"candidate {label} absolute maximum")


def _relative_lower_rule(*, name: str, baseline: Any, candidate: Any, policy: dict[str, Any],
                         key: str, default: float, label: str) -> GateRule:
    threshold = _number(policy, key, default)
    before, after = _metric(baseline), _metric(candidate)
    if before is None or after is None:
        return _unavailable_rule(name, f">= {threshold}", f"baseline or candidate {label} is not available")
    delta = after - before
    return GateRule(name, delta, f">= {threshold}", delta >= threshold, f"candidate - baseline {label}")


def _relative_upper_rule(*, name: str, baseline: Any, candidate: Any, policy: dict[str, Any],
                         key: str, default: float, label: str) -> GateRule:
    threshold = _number(policy, key, default)
    before, after = _metric(baseline), _metric(candidate)
    if before is None or after is None:
        return _unavailable_rule(name, f"<= {threshold}", f"baseline or candidate {label} is not available")
    delta = after - before
    return GateRule(name, delta, f"<= {threshold}", delta <= threshold, f"candidate - baseline {label}")


def _coverage_rules(experiment: dict[str, Any], policy: dict[str, Any]) -> list[GateRule]:
    """Require complete paired Case/Trial evidence before a release decision."""

    comparison = experiment.get("comparison") or {}
    case_comparisons = comparison.get("case_comparisons")
    required_cases = int(_number(policy, "min_case_count", 8.0))
    required_trials = int(_number(
        policy, "required_trials_per_case", experiment.get("trial_count_required_per_case", 3.0)
    ))
    if required_cases < 1 or required_trials < 1:
        raise ValueError("policy min_case_count and required_trials_per_case must be positive")
    if not isinstance(case_comparisons, list):
        return [
            _unavailable_rule("paired_case_coverage", f">= {required_cases}", "case comparison evidence is not available"),
            _unavailable_rule("paired_trial_coverage", f"{required_trials} Trials per Case", "case comparison evidence is not available"),
        ]

    complete_cases = 0
    incomplete_cases = 0
    for item in case_comparisons:
        if not isinstance(item, dict):
            incomplete_cases += 1
            continue
        baseline = item.get("baseline") or {}
        candidate = item.get("candidate") or {}
        paired = _metric(item.get("paired_trial_count"))
        baseline_count = _metric(baseline.get("trial_count")) if isinstance(baseline, dict) else None
        candidate_count = _metric(candidate.get("trial_count")) if isinstance(candidate, dict) else None
        if baseline_count is None or candidate_count is None or paired is None:
            incomplete_cases += 1
        elif baseline_count >= required_trials and candidate_count >= required_trials and paired >= required_trials:
            complete_cases += 1
        else:
            incomplete_cases += 1
    return [
        GateRule("paired_case_coverage", float(complete_cases), f">= {required_cases}", complete_cases >= required_cases,
                 "Cases with complete baseline/candidate paired evidence"),
        GateRule("paired_trial_coverage", float(incomplete_cases), "== 0 incomplete Cases", incomplete_cases == 0,
                 f"each Case requires at least {required_trials} baseline, candidate, and paired Trials"),
    ]


def _efficiency_diagnostics(comparison: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Describe tail/efficiency movement without making it a hard Gate rule."""

    baseline = comparison.get("baseline") or {}
    candidate = comparison.get("candidate") or {}
    efficiency = comparison.get("efficiency") or {}
    efficiency_baseline = efficiency.get("baseline") or {}
    efficiency_candidate = efficiency.get("candidate") or {}
    average_duration_delta = _optional_delta(baseline.get("avg_duration_ms"), candidate.get("avg_duration_ms"))
    p95_duration_delta = _optional_delta(efficiency_baseline.get("p95_duration_ms"), efficiency_candidate.get("p95_duration_ms"))
    p50_duration_delta = _optional_delta(efficiency_baseline.get("p50_duration_ms"), efficiency_candidate.get("p50_duration_ms"))
    average_token_delta = _optional_delta(baseline.get("avg_model_tokens"), candidate.get("avg_model_tokens"))
    p95_token_delta = _optional_delta(efficiency_baseline.get("p95_model_tokens"), efficiency_candidate.get("p95_model_tokens"))
    p95_threshold = _number(policy, "max_p95_duration_ms_delta", 0.0)

    def latency_status(delta: float | None, threshold: float = 0.0) -> str:
        if delta is None:
            return "not_available"
        return "regressed" if delta > threshold else "within_policy"

    return {
        "blocking": False,
        "average_duration_ms": {
            "delta": average_duration_delta,
            "status": latency_status(average_duration_delta),
            "note": "diagnostic only; average latency does not block promotion",
        },
        "p95_duration_ms": {
            "delta": p95_duration_delta,
            "threshold": p95_threshold,
            "blocking": False,
            "status": latency_status(p95_duration_delta, p95_threshold),
            "note": "tail-latency signal; inspect slow Trials before promotion",
        },
        "p50_duration_ms": {"delta": p50_duration_delta, "status": latency_status(p50_duration_delta)},
        "model_tokens": {
            "average_delta": average_token_delta,
            "p95_delta": p95_token_delta,
            "status": "saved" if average_token_delta is not None and average_token_delta < 0 else "increased" if average_token_delta is not None and average_token_delta > 0 else "unchanged" if average_token_delta is not None else "not_available",
        },
    }


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
    reliability = comparison.get("reliability") or {}
    rules: list[GateRule] = []
    protocol = experiment.get("protocol") if isinstance(experiment.get("protocol"), dict) else None
    protocol_comparability = protocol.get("comparability") if isinstance(protocol, dict) and isinstance(protocol.get("comparability"), dict) else None
    level = protocol_comparability.get("level") if isinstance(protocol_comparability, dict) else None
    rules.append(GateRule(
        "protocol_strict_comparability", 1.0 if level == "strict" else 0.0, "== 1.0", level == "strict",
        f"experiment protocol comparability is {level!r}",
    ))
    rules.extend(_coverage_rules(experiment, policy))

    rules.extend([
        _absolute_lower_rule(name="candidate_completion_rate_minimum", value=candidate.get("completion_rate"),
                             policy=policy, key="min_candidate_completion_rate", default=1.0, label="completion rate"),
        _absolute_lower_rule(name="candidate_evaluation_pass_rate_minimum", value=candidate.get("evaluation_pass_rate"),
                             policy=policy, key="min_candidate_evaluation_pass_rate", default=1.0, label="evaluation pass rate"),
        _absolute_upper_rule(name="candidate_model_failed_rate_limit", value=candidate.get("model_failed_rate"),
                             policy=policy, key="max_candidate_model_failed_rate", default=0.0, label="model failed rate"),
        _absolute_upper_rule(name="candidate_trace_incomplete_rate_limit", value=candidate.get("trace_incomplete_rate"),
                             policy=policy, key="max_candidate_trace_incomplete_rate", default=0.0, label="trace incomplete rate"),
        _absolute_upper_rule(name="candidate_infra_failed_rate_limit", value=candidate.get("infra_failed_rate"),
                             policy=policy, key="max_candidate_infra_failed_rate", default=0.0, label="infrastructure failed rate"),
        _absolute_upper_rule(name="candidate_path_policy_violation_rate_limit", value=candidate.get("path_policy_violation_rate"),
                             policy=policy, key="max_candidate_path_policy_violation_rate", default=0.0, label="path policy violation rate"),
        _absolute_upper_rule(name="candidate_diff_policy_violation_rate_limit", value=candidate.get("diff_policy_violation_rate"),
                             policy=policy, key="max_candidate_diff_policy_violation_rate", default=0.0, label="diff policy violation rate"),
        _relative_lower_rule(name="completion_rate_non_regression", baseline=baseline.get("completion_rate"), candidate=candidate.get("completion_rate"),
                             policy=policy, key="min_completion_rate_delta", default=0.0, label="completion rate"),
        _relative_lower_rule(name="evaluation_pass_rate_non_regression", baseline=baseline.get("evaluation_pass_rate"), candidate=candidate.get("evaluation_pass_rate"),
                             policy=policy, key="min_evaluation_pass_rate_delta", default=0.0, label="evaluation pass rate"),
        _relative_upper_rule(name="model_failed_rate_non_regression", baseline=baseline.get("model_failed_rate"), candidate=candidate.get("model_failed_rate"),
                             policy=policy, key="max_model_failed_rate_delta", default=0.0, label="model failed rate"),
        _relative_upper_rule(name="trace_incomplete_rate_non_regression", baseline=baseline.get("trace_incomplete_rate"), candidate=candidate.get("trace_incomplete_rate"),
                             policy=policy, key="max_trace_incomplete_rate_delta", default=0.0, label="trace incomplete rate"),
        _relative_upper_rule(name="infra_failed_rate_non_regression", baseline=baseline.get("infra_failed_rate"), candidate=candidate.get("infra_failed_rate"),
                             policy=policy, key="max_infra_failed_rate_delta", default=0.0, label="infrastructure failed rate"),
        _relative_upper_rule(name="path_policy_violation_rate_non_regression", baseline=baseline.get("path_policy_violation_rate"), candidate=candidate.get("path_policy_violation_rate"),
                             policy=policy, key="max_path_policy_violation_rate_delta", default=0.0, label="path policy violation rate"),
        _relative_upper_rule(name="diff_policy_violation_rate_non_regression", baseline=baseline.get("diff_policy_violation_rate"), candidate=candidate.get("diff_policy_violation_rate"),
                             policy=policy, key="max_diff_policy_violation_rate_delta", default=0.0, label="diff policy violation rate"),
    ])
    baseline_tools, candidate_tools = _metric(baseline.get("avg_tool_calls")), _metric(candidate.get("avg_tool_calls"))
    if baseline_tools is None or candidate_tools is None:
        rules.append(_unavailable_rule("average_tool_calls_limit", "available cost measurements", "baseline or candidate tool-call average is not available"))
    else:
        rules.append(_cost_increase_rule(
            name="average_tool_calls_limit", baseline=baseline_tools, candidate=candidate_tools,
            ratio_policy_key="max_avg_tool_calls_ratio",
            absolute_zero_baseline_policy_key="max_avg_tool_calls_absolute_increase_when_baseline_zero",
            policy=policy, label="tool-call",
        ))

    baseline_tokens, candidate_tokens = _metric(baseline.get("avg_model_tokens")), _metric(candidate.get("avg_model_tokens"))
    if baseline_tokens is None or candidate_tokens is None:
        rules.append(_unavailable_rule("average_model_tokens_limit", "available cost measurements", "baseline or candidate token average is not available"))
    else:
        rules.append(_cost_increase_rule(
            name="average_model_tokens_limit", baseline=baseline_tokens, candidate=candidate_tokens,
            ratio_policy_key="max_avg_model_tokens_ratio",
            absolute_zero_baseline_policy_key="max_avg_model_tokens_absolute_increase_when_baseline_zero",
            policy=policy, label="token",
        ))

    raw_baseline = (reliability.get("baseline") or {})
    raw_candidate = (reliability.get("candidate") or {})
    baseline_consistency = raw_baseline.get("all_pass_at_k", raw_baseline.get("pass_at_k"))
    candidate_consistency = raw_candidate.get("all_pass_at_k", raw_candidate.get("pass_at_k"))
    rules.append(_relative_lower_rule(name="all_pass_at_3_non_regression", baseline=baseline_consistency, candidate=candidate_consistency,
                                      policy=policy, key="min_all_pass_at_k_delta", default=0.0, label="all-pass@k consistency"))
    rules.append(_relative_upper_rule(name="flaky_case_rate_non_regression", baseline=raw_baseline.get("flaky_case_rate"), candidate=raw_candidate.get("flaky_case_rate"),
                                      policy=policy, key="max_flaky_case_rate_delta", default=0.0, label="flaky case rate"))

    hard_failures = [rule.name for rule in rules if not rule.passed]
    correctness_reliability_rules = {
        "completion_rate_non_regression", "evaluation_pass_rate_non_regression",
        "model_failed_rate_non_regression", "trace_incomplete_rate_limit",
        "infra_failed_rate_limit", "path_policy_violation_rate_limit",
        "diff_policy_violation_rate_limit", "all_pass_at_3_non_regression",
        "flaky_case_rate_non_regression",
    }
    correctness_or_reliability_regressed = bool(correctness_reliability_rules.intersection(hard_failures))
    average_token_delta = _optional_delta(baseline.get("avg_model_tokens"), candidate.get("avg_model_tokens"))
    token_saving_cannot_offset = correctness_or_reliability_regressed and average_token_delta is not None and average_token_delta < 0
    decision_message = (
        "blocked: correctness or reliability regression is not offset by token savings"
        if token_saving_cannot_offset else
        "blocked by hard Gate rules" if hard_failures else
        "candidate meets hard promotion rules; review diagnostics"
    )
    evidence_inconclusive_rules = {
        "protocol_strict_comparability", "paired_case_coverage", "paired_trial_coverage",
    }
    unavailable = [rule.name for rule in rules if rule.actual is None]
    evidence_inconclusive = bool(evidence_inconclusive_rules.intersection(hard_failures) or unavailable)
    quality_failures = [
        name for name in hard_failures
        if name not in evidence_inconclusive_rules and name not in unavailable
    ]
    inconclusive = evidence_inconclusive and not quality_failures
    return {
        "schema_version": 3,
        "baseline_id": experiment.get("baseline_id"),
        "candidate_id": experiment.get("candidate_id"),
        "passed": all(rule.passed for rule in rules),
        "rules": [rule.as_dict() for rule in rules],
        "diagnostics": _efficiency_diagnostics(comparison, policy),
        "protocol": {
            "fingerprint": protocol.get("fingerprint") if protocol else None,
            "comparability": protocol_comparability or {"level": "legacy_unverified"},
        },
        "decision": {
            "status": "inconclusive" if inconclusive else "hold" if hard_failures else "promote",
            "hard_blocking_failures": hard_failures,
            "not_available": unavailable,
            "correctness_or_reliability_regressed": correctness_or_reliability_regressed,
            "average_model_tokens_delta": average_token_delta,
            "token_savings_cannot_offset": token_saving_cannot_offset,
            "message": "inconclusive: evidence is incomplete, unavailable, or not strictly comparable" if inconclusive else decision_message,
        },
        "evidence": {"comparison": comparison},
    }
