"""基于 Agent 实验比较报告执行确定性的发布 Gate。"""

from __future__ import annotations

import math
import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GateRule:
    name: str
    actual: float | None
    expected: str
    passed: bool
    message: str
    required: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "actual": self.actual,
            "expected": self.expected,
            "passed": self.passed,
            "message": self.message,
            "required": self.required,
        }


def _number(policy: dict[str, Any], key: str, default: float) -> float:
    value = policy.get(key, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"policy.{key} must be a finite number")
    return float(value)


def _optional_delta(before: Any, after: Any) -> float | None:
    """两个值都可用时，返回 candidate - baseline 的差值。"""

    if (
        isinstance(before, (int, float))
        and not isinstance(before, bool)
        and isinstance(after, (int, float))
        and not isinstance(after, bool)
    ):
        return float(after) - float(before)
    return None


def _metric(value: Any) -> float | None:
    """返回有限的 Gate 指标，不把缺失证据当作零。"""

    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    ):
        return float(value)
    return None


def _validate_arm_metrics(label: str, metrics: dict[str, Any]) -> None:
    """拒绝不可能的持久化指标，避免基于损坏证据作出决策。"""

    rate_fields = (
        "completion_rate", "evaluation_pass_rate", "test_pass_rate",
        "model_failed_rate", "trace_incomplete_rate", "environment_mismatch_rate", "infra_failed_rate",
        "path_policy_violation_rate", "diff_policy_violation_rate",
    )
    non_negative_fields = (
        "trial_count", "avg_duration_ms", "avg_tool_calls", "avg_model_tokens",
        "avg_added_lines", "avg_deleted_lines", "p50_duration_ms", "p95_duration_ms",
        "p50_model_tokens", "p95_model_tokens",
    )
    for field in rate_fields:
        if field not in metrics:
            continue
        value = _metric(metrics[field])
        if value is None or not 0.0 <= value <= 1.0:
            raise ValueError(f"experiment.comparison.{label}.{field} must be a finite rate between 0 and 1")
    for field in non_negative_fields:
        if field not in metrics or metrics[field] is None:
            continue
        value = _metric(metrics[field])
        if value is None or value < 0:
            raise ValueError(f"experiment.comparison.{label}.{field} must be a finite non-negative number")


def _unavailable_rule(name: str, expected: str, message: str) -> GateRule:
    return GateRule(name, None, expected, False, message)


def _cost_increase_rule(*, name: str, baseline: float, candidate: float,
                        ratio_policy_key: str, absolute_zero_baseline_policy_key: str,
                        policy: dict[str, Any], label: str) -> GateRule:
    """当基线为零、比例无定义时，改用绝对增量安全比较成本。"""

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
    """发布决策前要求 Case/Trial 成对证据完整。"""

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
    """描述尾延迟和效率变化，但不把它们作为硬性 Gate 规则。"""

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

    if average_token_delta is None:
        token_status = "not_available"
    elif average_token_delta < 0:
        token_status = "saved"
    elif average_token_delta > 0:
        token_status = "increased"
    else:
        token_status = "unchanged"

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
            "status": token_status,
        },
    }


def _quality_rules(
    baseline: dict[str, Any], candidate: dict[str, Any], policy: dict[str, Any]
) -> list[GateRule]:
    """生成正确性与故障率的绝对门槛和非回归规则。"""

    rules: list[GateRule] = []
    absolute_minimums = (
        ("completion_rate", "candidate_completion_rate_minimum", "min_candidate_completion_rate", "completion rate"),
        (
            "evaluation_pass_rate",
            "candidate_evaluation_pass_rate_minimum",
            "min_candidate_evaluation_pass_rate",
            "evaluation pass rate",
        ),
    )
    absolute_maximums = (
        ("model_failed_rate", "candidate_model_failed_rate_limit", "max_candidate_model_failed_rate", "model failed rate"),
        (
            "trace_incomplete_rate", "candidate_trace_incomplete_rate_limit",
            "max_candidate_trace_incomplete_rate", "trace incomplete rate",
        ),
        (
            "environment_mismatch_rate", "candidate_environment_mismatch_rate_limit",
            "max_candidate_environment_mismatch_rate", "runtime environment mismatch rate",
        ),
        (
            "infra_failed_rate", "candidate_infra_failed_rate_limit",
            "max_candidate_infra_failed_rate", "infrastructure failed rate",
        ),
        (
            "path_policy_violation_rate", "candidate_path_policy_violation_rate_limit",
            "max_candidate_path_policy_violation_rate", "path policy violation rate",
        ),
        (
            "diff_policy_violation_rate", "candidate_diff_policy_violation_rate_limit",
            "max_candidate_diff_policy_violation_rate", "diff policy violation rate",
        ),
    )
    non_regression_minimums = (
        ("completion_rate", "completion_rate_non_regression", "min_completion_rate_delta", "completion rate"),
        (
            "evaluation_pass_rate", "evaluation_pass_rate_non_regression",
            "min_evaluation_pass_rate_delta", "evaluation pass rate",
        ),
    )
    non_regression_maximums = (
        ("model_failed_rate", "model_failed_rate_non_regression", "max_model_failed_rate_delta", "model failed rate"),
        (
            "trace_incomplete_rate", "trace_incomplete_rate_non_regression",
            "max_trace_incomplete_rate_delta", "trace incomplete rate",
        ),
        (
            "environment_mismatch_rate", "environment_mismatch_rate_non_regression",
            "max_environment_mismatch_rate_delta", "runtime environment mismatch rate",
        ),
        (
            "infra_failed_rate", "infra_failed_rate_non_regression",
            "max_infra_failed_rate_delta", "infrastructure failed rate",
        ),
        (
            "path_policy_violation_rate", "path_policy_violation_rate_non_regression",
            "max_path_policy_violation_rate_delta", "path policy violation rate",
        ),
        (
            "diff_policy_violation_rate", "diff_policy_violation_rate_non_regression",
            "max_diff_policy_violation_rate_delta", "diff policy violation rate",
        ),
    )
    for metric, name, policy_key, label in absolute_minimums:
        rules.append(_absolute_lower_rule(
            name=name, value=candidate.get(metric), policy=policy,
            key=policy_key, default=1.0, label=label,
        ))
    for metric, name, policy_key, label in absolute_maximums:
        if metric == "environment_mismatch_rate" and metric not in candidate:
            continue
        rules.append(_absolute_upper_rule(
            name=name, value=candidate.get(metric), policy=policy,
            key=policy_key, default=0.0, label=label,
        ))
    for metric, name, policy_key, label in non_regression_minimums:
        rules.append(_relative_lower_rule(
            name=name, baseline=baseline.get(metric), candidate=candidate.get(metric),
            policy=policy, key=policy_key, default=0.0, label=label,
        ))
    for metric, name, policy_key, label in non_regression_maximums:
        if metric == "environment_mismatch_rate" and (metric not in baseline or metric not in candidate):
            continue
        rules.append(_relative_upper_rule(
            name=name, baseline=baseline.get(metric), candidate=candidate.get(metric),
            policy=policy, key=policy_key, default=0.0, label=label,
        ))
    return rules


def _cost_rule(
    baseline: dict[str, Any], candidate: dict[str, Any], policy: dict[str, Any],
    *, metric: str, name: str, ratio_key: str, zero_baseline_key: str, label: str,
) -> GateRule:
    """为一项平均成本生成规则；任一比较臂缺失时明确标记不可用。"""

    baseline_value = _metric(baseline.get(metric))
    candidate_value = _metric(candidate.get(metric))
    if baseline_value is None or candidate_value is None:
        return _unavailable_rule(
            name, "available cost measurements",
            f"baseline or candidate {label} average is not available",
        )
    return _cost_increase_rule(
        name=name, baseline=baseline_value, candidate=candidate_value,
        ratio_policy_key=ratio_key,
        absolute_zero_baseline_policy_key=zero_baseline_key,
        policy=policy, label=label,
    )


def _provenance_rules(experiment: dict[str, Any], policy: dict[str, Any]) -> list[GateRule]:
    """检查 Gate 所依赖的证据是谁采集的，避免把 Agent 自报当作平台观测。"""

    summaries = experiment.get("summaries")
    if not isinstance(summaries, dict):
        return []
    jobs = [job for summary in summaries.values() if isinstance(summary, dict) for job in summary.get("jobs", []) if isinstance(job, dict)]
    if not jobs or not any(isinstance(job.get("evidence_provenance"), dict) for job in jobs):
        # 历史汇总没有来源字段；保留其原有 Gate 语义，不能在 report-only 时倒灌新要求。
        return []
    core_fields = ("process_lifecycle", "test_result", "git_evidence")
    core_valid = all(
        isinstance(job.get("evidence_provenance"), dict)
        and all(job["evidence_provenance"].get(field) == "platform_observed" for field in core_fields)
        for job in jobs
    )
    rules = [GateRule("core_evidence_provenance", 1.0 if core_valid else 0.0, "== 1.0", core_valid, "lifecycle, test, and Git evidence must be platform observed")]
    accepted_cost_origins = policy.get("accepted_cost_evidence_origins", ["platform_observed", "framework_observed"])
    if not isinstance(accepted_cost_origins, list) or not all(isinstance(item, str) for item in accepted_cost_origins):
        raise ValueError("policy.accepted_cost_evidence_origins must be a string array")
    observed_cost_jobs = [job for job in jobs if job.get("model_tokens") is not None or job.get("tool_calls") is not None]
    if observed_cost_jobs:
        trusted = all(
            isinstance(job.get("evidence_provenance"), dict)
            and job["evidence_provenance"].get("model_usage", job["evidence_provenance"].get("tool_trace")) in accepted_cost_origins
            for job in observed_cost_jobs
        )
        rules.append(GateRule("cost_evidence_provenance", 1.0 if trusted else 0.0, "== 1.0", trusted, "observed cost evidence must use an accepted origin"))
    return rules


def evaluate_gate(experiment: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """不重新运行 Agent，直接评估候选版本是否可以晋级。

    可靠性和正确性采用失败关闭的硬性检查；效率指标允许策略容忍小幅成本变化，
    但会阻断显著回归。
    """

    comparison = experiment.get("comparison")
    if (
        not isinstance(comparison, dict)
        or not isinstance(comparison.get("baseline"), dict)
        or not isinstance(comparison.get("candidate"), dict)
    ):
        raise ValueError("experiment.comparison with baseline and candidate metrics is required")
    baseline, candidate = comparison["baseline"], comparison["candidate"]
    _validate_arm_metrics("baseline", baseline)
    _validate_arm_metrics("candidate", candidate)
    reliability = comparison.get("reliability") or {}
    rules: list[GateRule] = []
    protocol = experiment.get("protocol") if isinstance(experiment.get("protocol"), dict) else None
    protocol_comparability = (
        protocol.get("comparability")
        if isinstance(protocol, dict) and isinstance(protocol.get("comparability"), dict)
        else None
    )
    level = protocol_comparability.get("level") if isinstance(protocol_comparability, dict) else None
    rules.append(GateRule(
        "protocol_strict_comparability", 1.0 if level == "strict" else 0.0, "== 1.0", level == "strict",
        f"experiment protocol comparability is {level!r}",
    ))
    rules.extend(_coverage_rules(experiment, policy))

    rules.extend(_quality_rules(baseline, candidate, policy))
    rules.extend(_provenance_rules(experiment, policy))
    cost_rules = [_cost_rule(
        baseline, candidate, policy,
        metric="avg_tool_calls", name="average_tool_calls_limit",
        ratio_key="max_avg_tool_calls_ratio",
        zero_baseline_key="max_avg_tool_calls_absolute_increase_when_baseline_zero",
        label="tool-call",
    ), _cost_rule(
        baseline, candidate, policy,
        metric="avg_model_tokens", name="average_model_tokens_limit",
        ratio_key="max_avg_model_tokens_ratio",
        zero_baseline_key="max_avg_model_tokens_absolute_increase_when_baseline_zero",
        label="token",
    )]
    require_cost_evidence = policy.get("require_cost_evidence", True)
    if not isinstance(require_cost_evidence, bool):
        raise ValueError("policy.require_cost_evidence must be a boolean")
    # Black-box 没有模型/工具观测时，不能因“未观测”被误判为成本回归；
    # 一旦指标已观测到，仍按同一阈值阻断真实回归。
    for rule in cost_rules:
        rules.append(GateRule(**{**rule.__dict__, "required": require_cost_evidence or rule.actual is not None}))

    baseline_reliability = reliability.get("baseline") or {}
    candidate_reliability = reliability.get("candidate") or {}
    baseline_consistency = baseline_reliability.get("all_pass_at_k", baseline_reliability.get("pass_at_k"))
    candidate_consistency = candidate_reliability.get("all_pass_at_k", candidate_reliability.get("pass_at_k"))
    rules.append(_relative_lower_rule(
        name="all_pass_at_3_non_regression",
        baseline=baseline_consistency,
        candidate=candidate_consistency,
        policy=policy,
        key="min_all_pass_at_k_delta",
        default=0.0,
        label="all-pass@k consistency",
    ))
    rules.append(_relative_upper_rule(
        name="flaky_case_rate_non_regression",
        baseline=baseline_reliability.get("flaky_case_rate"),
        candidate=candidate_reliability.get("flaky_case_rate"),
        policy=policy,
        key="max_flaky_case_rate_delta",
        default=0.0,
        label="flaky case rate",
    ))

    hard_failures = [rule.name for rule in rules if not rule.passed and rule.required]
    correctness_reliability_rules = {
        "candidate_completion_rate_minimum", "candidate_evaluation_pass_rate_minimum",
        "candidate_model_failed_rate_limit", "candidate_trace_incomplete_rate_limit",
        "candidate_environment_mismatch_rate_limit",
        "candidate_infra_failed_rate_limit", "candidate_path_policy_violation_rate_limit",
        "candidate_diff_policy_violation_rate_limit",
        "completion_rate_non_regression", "evaluation_pass_rate_non_regression",
        "model_failed_rate_non_regression", "trace_incomplete_rate_non_regression",
        "environment_mismatch_rate_non_regression",
        "infra_failed_rate_non_regression", "path_policy_violation_rate_non_regression",
        "diff_policy_violation_rate_non_regression", "all_pass_at_3_non_regression",
        "flaky_case_rate_non_regression",
    }
    correctness_or_reliability_regressed = bool(correctness_reliability_rules.intersection(hard_failures))
    average_token_delta = _optional_delta(baseline.get("avg_model_tokens"), candidate.get("avg_model_tokens"))
    token_saving_cannot_offset = (
        correctness_or_reliability_regressed
        and average_token_delta is not None
        and average_token_delta < 0
    )
    decision_message = (
        "blocked: correctness or reliability regression is not offset by token savings"
        if token_saving_cannot_offset else
        "blocked by hard Gate rules" if hard_failures else
        "candidate meets hard promotion rules; review diagnostics"
    )
    evidence_inconclusive_rules = {
        "protocol_strict_comparability", "paired_case_coverage", "paired_trial_coverage",
        "core_evidence_provenance", "cost_evidence_provenance",
    }
    unavailable = [rule.name for rule in rules if rule.actual is None]
    required_unavailable = [rule.name for rule in rules if rule.actual is None and rule.required]
    evidence_inconclusive = bool(evidence_inconclusive_rules.intersection(hard_failures) or required_unavailable)
    quality_failures = [
        name for name in hard_failures
        if name not in evidence_inconclusive_rules and name not in unavailable
    ]
    inconclusive = evidence_inconclusive and not quality_failures
    return {
        "schema_version": 4,
        "baseline_id": experiment.get("baseline_id"),
        "candidate_id": experiment.get("candidate_id"),
        "passed": not hard_failures,
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
        "evidence_policy": {
            "require_cost_evidence": require_cost_evidence,
            "fingerprint": "sha256:" + hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest(),
        },
        "evidence": {"comparison": comparison},
    }
