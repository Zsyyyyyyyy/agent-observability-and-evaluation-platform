"""只读校验已完成 Experiment Runtime 的产物完整性。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from regression_lab.manifest import ManifestError, safe_child_path
from regression_lab.protocol import protocol_fingerprint
from regression_lab.schema import validate_trace


_SUMMARY_PROJECTION_FIELDS = (
    "status",
    "trace_id",
    "agent_source_hash",
    "expected_agent_source_hash",
    "agent_source_hash_matches_protocol",
)


def _read_object(path: Path, issues: list[dict[str, str]], root: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        issues.append({
            "code": "artifact_unreadable",
            "path": _display_path(path, root),
            "message": f"JSON object is missing or invalid: {type(exc).__name__}",
        })
        return None
    if not isinstance(value, dict):
        issues.append({
            "code": "artifact_not_object",
            "path": _display_path(path, root),
            "message": "artifact root must be a JSON object",
        })
        return None
    return value


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.name


def _issue(issues: list[dict[str, str]], code: str, path: Path, root: Path, message: str) -> None:
    issues.append({"code": code, "path": _display_path(path, root), "message": message})


def _artifact_path(value: Any, *, result_dir: Path, runtime: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    configured = Path(value)
    candidates = [configured] if configured.is_absolute() else [result_dir / configured, runtime / configured]
    return next((
        candidate.resolve() for candidate in candidates
        if candidate.is_file() and candidate.resolve().is_relative_to(runtime)
    ), None)


def _verify_trial(
    root: Path,
    *,
    label: str,
    job: dict[str, Any],
    protocol_fingerprint: str | None,
    expected_source: Any,
    issues: list[dict[str, str]],
) -> tuple[tuple[str, str, int] | None, int]:
    """校验一个 Trial 的汇总投影、选定 Attempt、源码身份和 Trace。"""

    case_id = job.get("case_id")
    job_id = job.get("job_id")
    trial_index = job.get("trial_index")
    if not isinstance(case_id, str) or not isinstance(job_id, str) or not isinstance(trial_index, int):
        _issue(
            issues, "job_identity_invalid", root / "experiment.json", root,
            f"summary {label!r} contains an invalid Job identity",
        )
        return None, 0

    identity = (label, job_id, trial_index)
    try:
        agent_dir = safe_child_path(root, label, "agent label")
        case_dir = safe_child_path(agent_dir, case_id, "case id")
        job_dir = safe_child_path(case_dir, job_id, "job id")
    except ManifestError as exc:
        _issue(issues, "job_path_invalid", root / "experiment.json", root, str(exc))
        return identity, 0

    checks = 1
    result_path = job_dir / "result.json"
    result = _read_object(result_path, issues, root)
    if result is None:
        return identity, checks
    for field in _SUMMARY_PROJECTION_FIELDS:
        if job.get(field) != result.get(field):
            _issue(
                issues, "summary_projection_mismatch", result_path, root,
                f"summary field {field!r} does not match selected Result",
            )

    checks += 1
    selected_path = job_dir / "selected-attempt.json"
    selected = _read_object(selected_path, issues, root)
    if selected is None:
        return identity, checks
    attempt_id = selected.get("attempt_id")
    if selected.get("job_id") != job_id or not isinstance(attempt_id, str):
        _issue(
            issues, "selected_attempt_identity_invalid", selected_path, root,
            "selected Attempt does not belong to this Job",
        )
        return identity, checks
    try:
        attempt_dir = safe_child_path(job_dir / "attempts", attempt_id, "attempt id")
    except ManifestError as exc:
        _issue(issues, "selected_attempt_path_invalid", selected_path, root, str(exc))
        return identity, checks

    checks += 2
    attempt_result_path = attempt_dir / "result.json"
    attempt_manifest_path = attempt_dir / "attempt-manifest.json"
    attempt_result = _read_object(attempt_result_path, issues, root)
    attempt_manifest = _read_object(attempt_manifest_path, issues, root)
    if attempt_result is None or attempt_manifest is None:
        return identity, checks

    digest = "sha256:" + hashlib.sha256(attempt_result_path.read_bytes()).hexdigest()
    if attempt_manifest.get("result_sha256") != digest:
        _issue(
            issues, "attempt_result_digest_mismatch", attempt_manifest_path, root,
            "selected Attempt Result bytes do not match its terminal manifest",
        )
    if attempt_manifest.get("attempt_id") != attempt_id or attempt_manifest.get("job_id") != job_id:
        _issue(
            issues, "attempt_manifest_identity_mismatch", attempt_manifest_path, root,
            "Attempt manifest identity does not match the selection",
        )
    published_result = dict(result)
    published_result.pop("attempt_path", None)
    if published_result != attempt_result:
        _issue(
            issues, "selected_result_projection_mismatch", result_path, root,
            "published Result is not the selected immutable Attempt Result",
        )

    if result.get("protocol_fingerprint") != protocol_fingerprint:
        _issue(
            issues, "trial_protocol_mismatch", result_path, root,
            "selected Result does not reference the Experiment Protocol",
        )
    if isinstance(expected_source, str) and result.get("agent_source_hash") != expected_source:
        _issue(
            issues, "trial_agent_source_mismatch", result_path, root,
            "selected Result Agent source does not match the frozen version",
        )

    checks += 1
    trace_path = _artifact_path(result.get("trace_path"), result_dir=job_dir, runtime=root)
    recorded_validation = result.get("trace_validation")
    if not isinstance(recorded_validation, dict) or not isinstance(recorded_validation.get("valid"), bool):
        _issue(
            issues, "trace_validation_missing", result_path, root,
            "selected Result must record whether Trace evidence is valid",
        )
    elif trace_path is None and recorded_validation["valid"]:
        _issue(
            issues, "selected_trace_missing", result_path, root,
            "selected Result claims a valid Trace but the file is missing",
        )
    elif trace_path is not None:
        actual_validation = validate_trace(
            trace_path,
            expected_trace_id=result.get("trace_id") if isinstance(result.get("trace_id"), str) else None,
            expected_trial_id=result.get("trial_id") if isinstance(result.get("trial_id"), str) else None,
        )
        if actual_validation.valid != recorded_validation.get("valid"):
            _issue(
                issues, "trace_validation_mismatch", trace_path, root,
                "Trace bytes do not match the validation state stored in Result",
            )
    return identity, checks


def verify_experiment_runtime(runtime: str | Path) -> dict[str, Any]:
    """校验协议、执行计划、选定 Attempt、Trace 和 Gate 证据绑定关系。"""

    root = Path(runtime).resolve()
    issues: list[dict[str, str]] = []
    if not root.is_dir():
        return {
            "schema_version": 1,
            "kind": "experiment_integrity",
            "valid": False,
            "trial_count": 0,
            "checks": 0,
            "issues": [{"code": "runtime_missing", "path": str(root), "message": "runtime directory does not exist"}],
        }

    experiment = _read_object(root / "experiment.json", issues, root)
    protocol = _read_object(root / "protocol.json", issues, root)
    plan = _read_object(root / "execution-plan.json", issues, root)
    if experiment is None or protocol is None or plan is None:
        return {
            "schema_version": 1, "kind": "experiment_integrity", "valid": False,
            "trial_count": 0, "checks": 3, "issues": issues,
        }

    checks = 3
    stored_fingerprint = protocol.get("protocol_fingerprint")
    if not isinstance(stored_fingerprint, str) or stored_fingerprint != protocol_fingerprint(protocol):
        _issue(issues, "protocol_fingerprint_mismatch", root / "protocol.json", root,
               "protocol contents do not match the stored fingerprint")
    report_protocol = experiment.get("protocol") if isinstance(experiment.get("protocol"), dict) else {}
    if report_protocol.get("fingerprint") != stored_fingerprint:
        _issue(issues, "experiment_protocol_mismatch", root / "experiment.json", root,
               "experiment report does not reference protocol.json")

    expected_sources = {
        item.get("label"): item.get("agent_source_hash")
        for item in protocol.get("agents", [])
        if isinstance(item, dict) and isinstance(item.get("label"), str)
    }
    summaries = experiment.get("summaries")
    if not isinstance(summaries, dict):
        _issue(issues, "summaries_missing", root / "experiment.json", root,
               "experiment summaries must be an object")
        summaries = {}

    observed_jobs: set[tuple[str, str, int]] = set()
    trial_count = 0
    for label, summary in summaries.items():
        if not isinstance(label, str) or not isinstance(summary, dict):
            continue
        jobs = summary.get("jobs")
        if not isinstance(jobs, list):
            _issue(issues, "summary_jobs_missing", root / "experiment.json", root,
                   f"summary {label!r} does not contain a jobs list")
            continue
        if summary.get("job_count") != len(jobs):
            _issue(issues, "summary_job_count_mismatch", root / "experiment.json", root,
                   f"summary {label!r} job_count does not match its jobs")
        for job in jobs:
            if not isinstance(job, dict):
                continue
            identity, trial_checks = _verify_trial(
                root,
                label=label,
                job=job,
                protocol_fingerprint=stored_fingerprint if isinstance(stored_fingerprint, str) else None,
                expected_source=expected_sources.get(label),
                issues=issues,
            )
            checks += trial_checks
            if identity is not None:
                observed_jobs.add(identity)
                trial_count += 1

    entries = plan.get("entries")
    if not isinstance(entries, list):
        _issue(issues, "execution_plan_entries_missing", root / "execution-plan.json", root,
               "execution plan entries must be a list")
    else:
        planned_jobs = {
            (entry.get("agent_label"), entry.get("job_id"), entry.get("trial_index"))
            for entry in entries if isinstance(entry, dict)
        }
        if planned_jobs != observed_jobs:
            _issue(issues, "execution_plan_projection_mismatch", root / "execution-plan.json", root,
                   "planned Jobs and Experiment summary Jobs differ")

    gate_path = root / "gate-report.json"
    if gate_path.is_file():
        gate = _read_object(gate_path, issues, root)
        checks += 1
        evidence = gate.get("evidence") if isinstance(gate, dict) and isinstance(gate.get("evidence"), dict) else {}
        if gate is not None and evidence.get("comparison") != experiment.get("comparison"):
            _issue(issues, "gate_evidence_mismatch", gate_path, root,
                   "Gate evidence is stale or belongs to another Experiment report")

    return {
        "schema_version": 1,
        "kind": "experiment_integrity",
        "valid": not issues,
        "trial_count": trial_count,
        "checks": checks,
        "issues": issues,
    }
