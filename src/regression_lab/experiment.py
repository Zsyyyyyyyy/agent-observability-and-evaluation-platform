"""展开实验，并对基线版本与候选版本进行确定性比较。"""

from __future__ import annotations

from collections import defaultdict
from math import ceil
from random import Random
from statistics import mean, median
from typing import Any, Iterable

from regression_lab.behavior import aggregate_behavior
from regression_lab.behavior_diff import behavior_deltas
from regression_lab.attribution import aggregate_attribution


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


def _numbers(jobs: list[dict[str, Any]], field: str) -> list[float]:
    """返回数值型测量结果，不将异常数据静默转换为零。"""

    values: list[float] = []
    for job in jobs:
        value = job.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return values


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    """返回最近秩百分位数；没有测量值时返回 None。"""

    if not values:
        return None
    ordered = sorted(values)
    return ordered[ceil(percentile * len(ordered)) - 1]


def _valid_pass(job: dict[str, Any]) -> bool:
    """判断一个 Trial 是否可以作为可靠性指标的有效证据。

    较早版本持久化的汇总结果没有 ``trace_valid`` 字段。由于当时的评测已经
    包含 TraceCompletenessEvaluator，兼容旧数据的回退逻辑可以支持 report-only
    重建；新汇总结果则会显式保存这个字段。
    """

    trace_valid = job.get("trace_valid")
    return (
        job.get("status") == "completed"
        and job.get("evaluation_passed") is True
        and (trace_valid is True or trace_valid is None)
    )


def _metrics(summary: dict[str, Any]) -> dict[str, float | None]:
    jobs = summary.get("jobs", [])
    count = len(jobs) or 1
    completed = sum(job.get("status") == "completed" for job in jobs)
    evaluated = sum(bool(job.get("evaluation_passed")) for job in jobs)
    tests = sum(bool(job.get("test_passed")) for job in jobs)
    model_failed = sum(job.get("status") == "model_failed" for job in jobs)
    trace_incomplete = sum(job.get("status") == "trace_incomplete" for job in jobs)
    environment_mismatch = sum(job.get("status") == "environment_mismatch" for job in jobs)
    infra_failed = sum(job.get("status") == "infra_failed" for job in jobs)
    path_violations = sum(job.get("path_policy_passed") is False for job in jobs)
    diff_violations = sum(job.get("diff_policy_violated") is True for job in jobs)
    durations = _numbers(jobs, "duration_ms")
    tokens = _numbers(jobs, "model_tokens")
    tool_calls = _numbers(jobs, "tool_calls")
    return {
        "trial_count": float(len(jobs)),
        "completion_rate": completed / count,
        "evaluation_pass_rate": evaluated / count,
        "test_pass_rate": tests / count,
        "model_failed_rate": model_failed / count,
        "trace_incomplete_rate": trace_incomplete / count,
        "environment_mismatch_rate": environment_mismatch / count,
        "infra_failed_rate": infra_failed / count,
        "path_policy_violation_rate": path_violations / count,
        "diff_policy_violation_rate": diff_violations / count,
        "avg_tool_calls": mean(tool_calls) if tool_calls else None,
        "avg_duration_ms": mean(durations) if durations else 0.0,
        "avg_added_lines": mean(_numbers(jobs, "added_lines")) if _numbers(jobs, "added_lines") else 0.0,
        "avg_deleted_lines": mean(_numbers(jobs, "deleted_lines")) if _numbers(jobs, "deleted_lines") else 0.0,
        "avg_model_tokens": mean(tokens) if tokens else None,
        "p50_duration_ms": median(durations) if durations else None,
        "p95_duration_ms": _nearest_rank(durations, 0.95),
        "p50_model_tokens": median(tokens) if tokens else None,
        "p95_model_tokens": _nearest_rank(tokens, 0.95),
    }


def _group_by_case(jobs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        case_id = job.get("case_id")
        if isinstance(case_id, str) and case_id:
            grouped[case_id].append(job)
    return {
        case_id: sorted(group, key=lambda job: (job.get("trial_index") is None, job.get("trial_index"), job.get("job_id", "")))
        for case_id, group in grouped.items()
    }


def _case_reliability(jobs: list[dict[str, Any]], required_trials: int) -> dict[str, Any]:
    grouped = _group_by_case(jobs)
    eligible: list[tuple[str, list[dict[str, Any]]]] = []
    unavailable: list[dict[str, Any]] = []
    for case_id, group in sorted(grouped.items()):
        if len(group) < required_trials:
            unavailable.append({"case_id": case_id, "reason": "insufficient_trials", "trial_count": len(group)})
        else:
            eligible.append((case_id, group))
    all_pass_at_k = sum(all(_valid_pass(job) for job in group[:required_trials]) for _, group in eligible)
    flaky = sum(any(_valid_pass(job) for job in group) and not all(_valid_pass(job) for job in group) for _, group in eligible)
    count = len(eligible)
    return {
        "required_trials_per_case": required_trials,
        "case_count": len(grouped),
        "eligible_case_count": count,
        # 这不是标准意义上的 Pass@k（通常表示 k 次尝试中至少成功一次）。
        # 这里要求一个 Case 的所有必要重复运行都通过，因此衡量的是一致性。
        "all_pass_at_k": all_pass_at_k / count if count else None,
        "all_pass_at_k_case_count": all_pass_at_k,
        "flaky_case_rate": flaky / count if count else None,
        "flaky_case_count": flaky,
        "unavailable_cases": unavailable,
    }


def _trial_view(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "trial_index": job.get("trial_index"),
        "status": job.get("status"),
        "valid_pass": _valid_pass(job),
        "duration_ms": job.get("duration_ms"),
        "model_tokens": job.get("model_tokens"),
        "tool_calls": job.get("tool_calls"),
    }


def _case_comparisons(baseline_jobs: list[dict[str, Any]], candidate_jobs: list[dict[str, Any]], required_trials: int) -> list[dict[str, Any]]:
    baseline_cases, candidate_cases = _group_by_case(baseline_jobs), _group_by_case(candidate_jobs)
    comparisons: list[dict[str, Any]] = []
    for case_id in sorted(set(baseline_cases) | set(candidate_cases)):
        baseline_group, candidate_group = baseline_cases.get(case_id, []), candidate_cases.get(case_id, [])
        baseline_by_index = {job.get("trial_index"): job for job in baseline_group if job.get("trial_index") is not None}
        candidate_by_index = {job.get("trial_index"): job for job in candidate_group if job.get("trial_index") is not None}
        paired = []
        for trial_index in sorted(set(baseline_by_index) & set(candidate_by_index)):
            before, after = baseline_by_index[trial_index], candidate_by_index[trial_index]
            paired.append({
                "trial_index": trial_index,
                "baseline": _trial_view(before),
                "candidate": _trial_view(after),
                "delta": {
                    key: after.get(key) - before.get(key)
                    if isinstance(after.get(key), (int, float)) and not isinstance(after.get(key), bool)
                    and isinstance(before.get(key), (int, float)) and not isinstance(before.get(key), bool)
                    else None
                    for key in ("duration_ms", "model_tokens", "tool_calls")
                },
            })
        comparisons.append({
            "case_id": case_id,
            "baseline": {"trial_count": len(baseline_group), "valid_pass_count": sum(_valid_pass(job) for job in baseline_group),
                         "all_pass_at_k": all(_valid_pass(job) for job in baseline_group[:required_trials]) if len(baseline_group) >= required_trials else None},
            "candidate": {"trial_count": len(candidate_group), "valid_pass_count": sum(_valid_pass(job) for job in candidate_group),
                          "all_pass_at_k": all(_valid_pass(job) for job in candidate_group[:required_trials]) if len(candidate_group) >= required_trials else None},
            "paired_trial_count": len(paired),
            "paired_trials": paired,
        })
    return comparisons


def _percentile(values: list[float], percentile: float) -> float | None:
    """通过线性插值计算百分位数，用于确定性的 Bootstrap 置信区间。"""

    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower, upper = int(position), ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _paired_statistics(case_comparisons: list[dict[str, Any]], *, resamples: int = 2_000, seed: int = 20_260_812) -> dict[str, Any]:
    """在保留每个 Case 的重复 Trial 聚类结构的前提下估计配对差值。

    如果单独重采样 Trial，同一个任务的三次重复运行会被误认为三个相互独立的
    任务族。因此 Bootstrap 会有放回地采样 Case ID，并带上该 Case 的所有有效
    配对 Trial 差值。这个结果只用于稳定性诊断，不能单独作为 p 值或发布门禁。
    """

    metrics = ("duration_ms", "model_tokens", "tool_calls")
    clusters: dict[str, dict[str, list[float]]] = {}
    unavailable_pairs = 0
    for comparison in case_comparisons:
        case_id = comparison.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            continue
        metric_values = {metric: [] for metric in metrics}
        for pair in comparison.get("paired_trials", []):
            before, after = pair.get("baseline", {}), pair.get("candidate", {})
            if not before.get("valid_pass") or not after.get("valid_pass"):
                unavailable_pairs += 1
                continue
            for metric in metrics:
                value = (pair.get("delta") or {}).get(metric)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    metric_values[metric].append(float(value))
        if any(metric_values.values()):
            clusters[case_id] = metric_values

    case_ids = sorted(clusters)
    result: dict[str, Any] = {
        "method": "clustered_case_bootstrap",
        "confidence_level": 0.95,
        "resamples": resamples,
        "seed": seed,
        "eligible_case_count": len(case_ids),
        "excluded_paired_trial_count": unavailable_pairs,
        "metrics": {},
    }
    if not case_ids:
        result["conclusion"] = {"level": "not_available", "reason": "no valid paired Trial measurements"}
        return result

    rng = Random(seed)
    for metric in metrics:
        observed = [value for case_id in case_ids for value in clusters[case_id][metric]]
        case_medians = {case_id: median(clusters[case_id][metric]) for case_id in case_ids if clusters[case_id][metric]}
        if not observed:
            result["metrics"][metric] = {"available": False, "reason": "no valid paired measurements"}
            continue
        samples: list[float] = []
        for _ in range(resamples):
            sampled_values = [
                value
                for case_id in (rng.choice(case_ids) for _ in case_ids)
                for value in clusters[case_id][metric]
            ]
            if sampled_values:
                samples.append(mean(sampled_values))
        ci_low, ci_high = _percentile(samples, 0.025), _percentile(samples, 0.975)
        wins = sum(value < 0 for value in case_medians.values())
        losses = sum(value > 0 for value in case_medians.values())
        ties = len(case_medians) - wins - losses
        direction = "inconclusive"
        if ci_high is not None and ci_high < 0:
            direction = "likely_lower"
        elif ci_low is not None and ci_low > 0:
            direction = "likely_higher"
        result["metrics"][metric] = {
            "available": True,
            "paired_trial_count": len(observed),
            "point_estimate_mean_delta": mean(observed),
            "point_estimate_median_delta": median(observed),
            "ci95": {"low": ci_low, "high": ci_high},
            "case_outcomes": {"candidate_lower": wins, "candidate_higher": losses, "tied": ties},
            "direction": direction,
        }

    latency = result["metrics"].get("duration_ms", {})
    if len(case_ids) < 8:
        conclusion = {"level": "limited_coverage", "reason": f"{len(case_ids)} eligible Cases; target is at least 8 before a broad performance claim."}
    elif latency.get("direction") == "likely_lower":
        conclusion = {"level": "observed_latency_improvement", "reason": "the paired Case bootstrap interval for latency is below zero; assess cost metrics separately."}
    else:
        conclusion = {"level": "inconclusive", "reason": "the paired Case bootstrap interval crosses zero or valid pairs are incomplete."}
    result["conclusion"] = conclusion
    return result


def _reliability_confidence(case_comparisons: list[dict[str, Any]], *, resamples: int = 2_000, seed: int = 20_260_813) -> dict[str, Any]:
    """为 Case 级一致性与 flaky 差异提供聚类 Bootstrap 区间。"""

    values: list[tuple[float, float]] = []
    for comparison in case_comparisons:
        baseline = comparison.get("baseline") or {}
        candidate = comparison.get("candidate") or {}
        before, after = baseline.get("all_pass_at_k"), candidate.get("all_pass_at_k")
        if isinstance(before, bool) and isinstance(after, bool):
            pairs = comparison.get("paired_trials") or []
            baseline_flaky = any(not item.get("baseline", {}).get("valid_pass") for item in pairs) and any(item.get("baseline", {}).get("valid_pass") for item in pairs)
            candidate_flaky = any(not item.get("candidate", {}).get("valid_pass") for item in pairs) and any(item.get("candidate", {}).get("valid_pass") for item in pairs)
            values.append((float(after) - float(before), float(candidate_flaky) - float(baseline_flaky)))
    if not values:
        return {"method": "clustered_case_bootstrap", "confidence_level": 0.95, "eligible_case_count": 0, "metrics": {}, "conclusion": "not_available"}
    rng = Random(seed)
    metrics: dict[str, Any] = {}
    for index, name in enumerate(("all_pass_at_k_delta", "flaky_case_rate_delta")):
        samples = [mean(rng.choice(values)[index] for _ in values) for _ in range(resamples)]
        low, high = _percentile(samples, 0.025), _percentile(samples, 0.975)
        metrics[name] = {"point_estimate": mean(value[index] for value in values), "ci95": {"low": low, "high": high}, "available": True}
    return {"method": "clustered_case_bootstrap", "confidence_level": 0.95, "resamples": resamples, "seed": seed, "eligible_case_count": len(values), "metrics": metrics, "conclusion": "limited_coverage" if len(values) < 8 else "available"}


def compare_summaries(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    required_trials_per_case: int = 3,
    baseline_version: str | None = None,
    candidate_version: str | None = None,
) -> dict[str, Any]:
    if required_trials_per_case < 1:
        raise ValueError("required_trials_per_case must be positive")
    baseline_metrics = _metrics(baseline)
    candidate_metrics = _metrics(candidate)
    deltas = {
        key: candidate_metrics[key] - baseline_metrics[key]
        for key in baseline_metrics
        if key != "trial_count"
        and baseline_metrics[key] is not None and candidate_metrics[key] is not None
    }
    classifications: dict[str, list[str]] = {"improved": [], "regressed": [], "unchanged": []}
    for key in ("completion_rate", "evaluation_pass_rate", "test_pass_rate"):
        delta = deltas.get(key)
        if delta is None:
            continue
        bucket = "improved" if delta > 0 else "regressed" if delta < 0 else "unchanged"
        classifications[bucket].append(key)
    for key in (
        "avg_tool_calls", "avg_duration_ms", "avg_model_tokens",
        "model_failed_rate", "trace_incomplete_rate", "environment_mismatch_rate", "infra_failed_rate",
        "path_policy_violation_rate", "diff_policy_violation_rate",
    ):
        delta = deltas.get(key)
        if delta is None:
            continue
        bucket = "improved" if delta < 0 else "regressed" if delta > 0 else "unchanged"
        classifications[bucket].append(key)
    baseline_reliability = _case_reliability(list(baseline.get("jobs", [])), required_trials_per_case)
    candidate_reliability = _case_reliability(list(candidate.get("jobs", [])), required_trials_per_case)
    reliability_delta = {
        key: candidate_reliability[key] - baseline_reliability[key]
        if baseline_reliability[key] is not None and candidate_reliability[key] is not None else None
        for key in ("all_pass_at_k", "flaky_case_rate")
    }
    case_comparisons = _case_comparisons(list(baseline.get("jobs", [])), list(candidate.get("jobs", [])), required_trials_per_case)
    statistics = _paired_statistics(case_comparisons)
    return {
        "metrics_version": 3,
        "baseline": baseline_metrics,
        "candidate": candidate_metrics,
        "delta": deltas,
        "classification": classifications,
        "reliability": {"baseline": baseline_reliability, "candidate": candidate_reliability, "delta": reliability_delta, "confidence": _reliability_confidence(case_comparisons)},
        "efficiency": {
            "baseline": {key: baseline_metrics[key] for key in ("p50_duration_ms", "p95_duration_ms", "p50_model_tokens", "p95_model_tokens")},
            "candidate": {key: candidate_metrics[key] for key in ("p50_duration_ms", "p95_duration_ms", "p50_model_tokens", "p95_model_tokens")},
        },
        "case_comparisons": case_comparisons,
        "statistics": statistics,
        "behavior": {
            "baseline": aggregate_behavior(list(baseline.get("jobs", []))),
            "candidate": aggregate_behavior(list(candidate.get("jobs", []))),
        },
        "behavior_diff": behavior_deltas(
            list(baseline.get("jobs", [])),
            list(candidate.get("jobs", [])),
            baseline_version=baseline_version,
            candidate_version=candidate_version,
        ),
        "failure_attribution": {
            "baseline": aggregate_attribution(list(baseline.get("jobs", []))),
            "candidate": aggregate_attribution(list(candidate.get("jobs", []))),
        },
    }
