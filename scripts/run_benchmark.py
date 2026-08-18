#!/usr/bin/env python3
"""Expand and execute a deterministic Benchmark Case Manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_lab.manifest import (
    ManifestError,
    expand_trials,
    load_manifest,
    safe_child_path,
    validate_manifest,
)
from regression_lab.adapters import AdapterCapabilities, AdapterError, get_adapter
from regression_lab.attempts import AttemptManager, AttemptPaths
from regression_lab.attribution import attribute_trial
from regression_lab.behavior import summarize_trial_behavior
from regression_lab.behavior_diff import snapshot_trial_behavior
from regression_lab.runner import run_with_deadline
from regression_lab.sandbox import DockerSandbox
from regression_lab.store import RunStore
from regression_lab.artifacts import write_json_atomically
from regression_lab.protocol import file_hash


REGRESSION = Path(__file__).resolve().parents[1]


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _job_fingerprint(
    job: dict[str, object], *, adapter_id: str, agent_version: str, use_docker: bool, replay_bash: bool, external_command: list[str] | None = None,
    expected_agent_source_hash: str | None = None, adapter_capabilities: dict[str, object] | None = None,
    external_observation_mode: str = "sdk",
) -> str:
    payload = {
        "job": job,
        "adapter_id": adapter_id,
        "agent_version": agent_version,
        "execution_mode": "docker" if use_docker else "unsafe_trusted_host",
        "replay_bash": replay_bash,
        "external_command": external_command,
        "expected_agent_source_hash": expected_agent_source_hash,
        "adapter_capabilities": adapter_capabilities,
        "external_observation_mode": external_observation_mode,
        "external_command_source_hash": _external_command_source_hash(external_command),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _external_command_source_hash(command: list[str] | None) -> str | None:
    """Hash the local external Agent entry point, never its absolute path."""

    if not command:
        return None
    candidate = next((Path(argument) for argument in reversed(command) if Path(argument).is_file()), None)
    return file_hash(candidate) if candidate else None


def _owned_job_dir(job_dir: Path, job_id: str, fingerprint: str) -> bool:
    marker = job_dir / "run-manifest.json"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload == {"schema_version": 2, "job_id": job_id, "fingerprint": fingerprint}


def _create_job_dir(job_dir: Path, job_id: str, fingerprint: str) -> None:
    job_dir.mkdir(parents=True)
    write_json_atomically(job_dir / "run-manifest.json", {"schema_version": 2, "job_id": job_id, "fingerprint": fingerprint})


def _timed_out_result(job: dict[str, object], timeout_seconds: int) -> dict[str, object]:
    """Persist a result even when the worker itself misses the trial deadline."""

    return {
        "trial_id": job["job_id"],
        "status": "timed_out",
        "trace_id": None,
        "error": f"parent runner deadline exceeded ({timeout_seconds}s)",
        "test_exit_code": -1,
        "scores": [],
        "evaluation": {"passed": False, "reason": "worker deadline exceeded"},
    }


def _is_reusable_result(result: dict[str, object]) -> bool:
    trace_valid = (result.get("trace_validation") or {}).get("valid") is True
    evaluation_passed = (result.get("evaluation") or {}).get("passed") is True
    return result.get("status") == "completed" and evaluation_passed and trace_valid


def _attempt_status(result: dict[str, object]) -> str:
    """Map a worker result to the physical Attempt lifecycle, not Gate success."""

    if result.get("status") == "timed_out":
        return "timed_out"
    if result.get("status") == "trace_incomplete":
        return "invalid"
    return "completed"


def _publish_existing_attempt(job_dir: Path, attempts: AttemptManager) -> dict[str, object] | None:
    """Restore the Job compatibility result from the selected Attempt projection."""

    selected = attempts.resolve_selected_attempt() or attempts.select_latest_terminal_attempt()
    if selected is None:
        return None
    attempt, result = selected
    _write_selected_result(job_dir, attempt, result)
    return result


def _sync_selected_store(run_store: Path, attempts: AttemptManager, attempt: AttemptPaths, result: dict[str, object]) -> None:
    """Publish SQLite only after the Artifact selector has chosen the Trial view."""

    scores = [score for score in result.get("scores", []) if isinstance(score, dict)]
    RunStore(run_store).record_selected_projection(result, scores, attempt.attempt_id)


def _may_retry_model_failure(attempts: AttemptManager, result: dict[str, object], max_retries: int) -> bool:
    """Allow only bounded retries for a provider-side model failure."""

    if result.get("status") != "model_failed":
        return False
    # ``max_retries`` is retries after the initial execution.
    return len(attempts.list_attempts()) < max_retries + 1


def _write_selected_result(job_dir: Path, attempt: AttemptPaths, result: dict[str, object]) -> None:
    """Publish the selected Attempt through the legacy Job-level result path."""

    selected = {
        **result,
        "attempt_id": attempt.attempt_id,
        "attempt_path": str(attempt.directory),
    }
    write_json_atomically(attempt.result, selected)
    write_json_atomically(job_dir / "result.json", selected)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", default=str(REGRESSION / ".runtime" / "benchmark"))
    parser.add_argument("--project-root", default=str(REGRESSION))
    parser.add_argument("--trials", type=int)
    parser.add_argument("--trial-index", type=int, action="append",
                        help="run only a selected 1-based Trial index; repeatable for orchestration")
    parser.add_argument("--docker", action="store_true", help="run tests and bash in Docker Sandbox (default)")
    parser.add_argument(
        "--unsafe-trusted-host",
        action="store_true",
        help="run a trusted local fixture on the host; disables the Docker safety boundary",
    )
    parser.add_argument("--bash", action="store_true", help="include a replayed Docker bash call")
    parser.add_argument("--adapter", default="react-agent", help="registered Agent adapter ID")
    parser.add_argument(
        "--replay-source",
        help="path to an external agent_entry.py; required only when executing readonly-replay",
    )
    parser.add_argument("--agent-version", help="Agent implementation version; defaults to the adapter version")
    parser.add_argument("--agent-profile", help="optional Agent operating-profile label recorded in the Trial")
    parser.add_argument("--external-command", help="JSON argv array for external-command, e.g. '[\"python3\", \"/path/agent.py\"]'")
    parser.add_argument("--adapter-capabilities", help="Evidence Capability JSON snapshot for an external-command Agent")
    parser.add_argument(
        "--external-observation-mode", choices=("sdk", "blackbox"), default="sdk",
        help="external-command evidence mode; sdk remains the legacy default",
    )
    parser.add_argument("--expected-agent-source-hash", help="frozen external Agent entry-point hash from the Experiment Protocol")
    parser.add_argument("--resume", action="store_true", help="reuse completed jobs and rerun incomplete jobs")
    parser.add_argument(
        "--rerun-invalid",
        action="store_true",
        help="with --resume, archive completed-but-invalid jobs and execute them again",
    )
    parser.add_argument(
        "--rerun-completed",
        action="store_true",
        help="with --resume, create a new Attempt for selected completed jobs; preserve prior evidence",
    )
    parser.add_argument("--dry-run", action="store_true", help="only validate and print expanded jobs")
    parser.add_argument("--protocol-fingerprint", help="platform-owned Experiment Protocol identity")
    parser.add_argument("--schedule-index", type=int, help="platform-owned interleaved execution position")
    args = parser.parse_args()
    use_docker = not args.unsafe_trusted_host
    if args.bash and not use_docker:
        parser.error("--bash requires Docker; --unsafe-trusted-host is incompatible")
    try:
        adapter = get_adapter(args.adapter)
    except AdapterError as exc:
        parser.error(str(exc))
    agent_version = args.agent_version or adapter.default_version
    external_command: list[str] | None = None
    if args.external_command:
        try:
            decoded = json.loads(args.external_command)
        except json.JSONDecodeError as exc:
            parser.error(f"--external-command must be a JSON argv array: {exc.msg}")
        if not isinstance(decoded, list) or not decoded or not all(isinstance(item, str) and item for item in decoded):
            parser.error("--external-command must be a non-empty JSON argv string array")
        external_command = decoded
    if adapter.adapter_id == "external-command" and not external_command:
        parser.error("--adapter external-command requires --external-command")
    if adapter.adapter_id != "external-command" and external_command:
        parser.error("--external-command is only valid with --adapter external-command")
    if adapter.adapter_id != "external-command" and args.external_observation_mode != "sdk":
        parser.error("--external-observation-mode is only valid with --adapter external-command")
    adapter_capabilities = adapter.evidence_capabilities
    if args.adapter_capabilities:
        if adapter.adapter_id != "external-command":
            parser.error("--adapter-capabilities is only valid with --adapter external-command")
        try:
            declared_capabilities = json.loads(args.adapter_capabilities)
        except json.JSONDecodeError as exc:
            parser.error(f"--adapter-capabilities must be a JSON object: {exc.msg}")
        adapter_capabilities = AdapterCapabilities.from_snapshot(declared_capabilities)
        if adapter_capabilities is None:
            parser.error("--adapter-capabilities must provide every AdapterCapabilities boolean field")
    replay_source: Path | None = None
    if adapter.adapter_id == "readonly-replay" and not args.dry_run:
        if not args.replay_source:
            parser.error("--replay-source is required when executing the optional readonly-replay bridge")
        replay_source = Path(args.replay_source).expanduser().resolve()
        if not replay_source.is_file():
            parser.error(f"external Agent source does not exist: {replay_source}")

    manifest_path = Path(args.manifest).resolve()
    try:
        manifest = load_manifest(manifest_path)
        validation = validate_manifest(manifest, args.project_root)
        if not validation.valid:
            print(json.dumps(validation.as_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
            return 2
        jobs = expand_trials(manifest, args.project_root, args.trials)
        if args.trial_index:
            selected = set(args.trial_index)
            if any(index < 1 for index in selected):
                raise ManifestError("trial_index must be positive")
            jobs = [job for job in jobs if int(job["trial_index"]) in selected]
            if not jobs:
                raise ManifestError("selected trial_index does not exist in this manifest")
    except (OSError, ManifestError) as exc:
        print(f"MANIFEST ERROR: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(json.dumps({"manifest": manifest["id"], "jobs": jobs}, ensure_ascii=False, indent=2))
        return 0
    if use_docker:
        available, detail = DockerSandbox.available()
        if not available:
            print(f"DOCKER UNAVAILABLE: {detail}", file=sys.stderr)
            return 2

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_store = output_dir / "runs.db"
    summaries: list[dict[str, object]] = []
    for job in jobs:
        fingerprint = _job_fingerprint(
            job, adapter_id=adapter.adapter_id, agent_version=agent_version,
            use_docker=use_docker, replay_bash=args.bash,
            external_command=external_command,
            expected_agent_source_hash=args.expected_agent_source_hash,
            adapter_capabilities=adapter_capabilities.as_dict(),
            external_observation_mode=args.external_observation_mode,
        )
        try:
            job_dir = safe_child_path(output_dir, job["job_id"], "job_id")
        except ManifestError as exc:
            print(f"OUTPUT PATH ERROR: {exc}", file=sys.stderr)
            return 2
        attempts = AttemptManager(
            job_dir, job_id=str(job["job_id"]), fingerprint=fingerprint,
            protocol_fingerprint=args.protocol_fingerprint, schedule_index=args.schedule_index,
        )
        existed_before_lock = job_dir.exists()
        if not existed_before_lock:
            _create_job_dir(job_dir, str(job["job_id"]), fingerprint)
        try:
            attempts.acquire_trial_lock()
            attempts.recover_orphaned_attempts()
        except RuntimeError as exc:
            print(f"TRIAL LOCK ERROR: {exc}", file=sys.stderr)
            return 2
        if existed_before_lock:
            if not _owned_job_dir(job_dir, str(job["job_id"]), fingerprint):
                print(f"REFUSING UNOWNED OUTPUT DIRECTORY: {job_dir}", file=sys.stderr)
                attempts.release_trial_lock()
                return 2
            existing_result = job_dir / "result.json"
            if args.resume and existing_result.exists():
                # A prior good Attempt always wins over a later transient
                # failure only under the legacy selector. Current runs read
                # the explicit Artifact projection without re-ranking it.
                result = _publish_existing_attempt(job_dir, attempts) or json.loads(existing_result.read_text(encoding="utf-8"))
                selected_attempt = attempts.resolve_selected_attempt()
                if selected_attempt is not None:
                    _sync_selected_store(run_store, attempts, selected_attempt[0], selected_attempt[1])
                if _is_reusable_result(result) and not args.rerun_completed:
                    summaries.append(_job_summary(job, result))
                    attempts.release_trial_lock()
                    continue
                retry_model_failure = _may_retry_model_failure(attempts, result, int(job.get("max_retries", 0)))
                # Explicit operator intent may retry any non-reusable evidence
                # (including timeout); automatic resume remains model-only.
                retry_invalid_evidence = args.rerun_invalid
                if not retry_model_failure and not retry_invalid_evidence and not args.rerun_completed:
                    summaries.append(_job_summary(job, result))
                    attempts.release_trial_lock()
                    continue
                if result.get("status") == "completed" and not (args.rerun_invalid or args.rerun_completed):
                    # Preserve an actual Agent failure as evidence unless the
                    # operator explicitly asks to retry invalid output.
                    summaries.append(_job_summary(job, result))
                    attempts.release_trial_lock()
                    continue
            if not args.resume:
                print(f"OUTPUT EXISTS: {job_dir}", file=sys.stderr)
                attempts.release_trial_lock()
                return 2

        attempt = attempts.create_attempt()
        worktree = attempt.worktree
        shutil.copytree(str(job["fixture_path"]), worktree)
        git("init", cwd=worktree)
        git("config", "user.email", "regression-lab@example.invalid", cwd=worktree)
        git("config", "user.name", "Regression Lab", cwd=worktree)
        git("add", ".", cwd=worktree)
        git("commit", "-m", "benchmark fixture baseline", cwd=worktree)

        test_command = str(job["test_command"])
        if not use_docker and test_command.startswith("python "):
            test_command = "python3.11 " + test_command[len("python "):]
        spec = {
            "trial_id": str(job["job_id"]),
            "agent_version": agent_version,
            "agent_profile": args.agent_profile,
            "adapter": adapter.as_spec(),
            "adapter_id": adapter.adapter_id,
            "adapter_capabilities": adapter_capabilities.as_dict(),
            "observation_mode": args.external_observation_mode if adapter.adapter_id == "external-command" else None,
            "case_id": job["case_id"],
            "prompt": job["prompt"],
            "worktree": str(worktree),
            "test_command": test_command,
            "sandbox": {**job["sandbox"], "image": "python:3.11-slim"} if use_docker else None,
            # Docker 只决定测试隔离方式；Trial 时限始终来自 Benchmark。
            "trial_timeout_seconds": int(job["sandbox"]["timeout_seconds"]),
            "replay_bash": args.bash,
            "allowed_paths": job["allowed_paths"],
            "forbidden_paths": job["forbidden_paths"],
            "allowed_tools": job["tool_policy"]["allow"],
            "denied_tools": job["tool_policy"]["deny"],
            "failure_mode": job.get("failure_mode"),
            "budget": job["budget"],
            "max_tokens": job["max_tokens"],
            "attempt_id": attempt.attempt_id,
            "trace_output": str(attempt.trace),
            "result_output": str(attempt.result),
            # Worker output is immutable Attempt evidence. The platform writes
            # the SQLite projection only after selected-attempt.json exists.
            "run_store": None,
            "protocol_fingerprint": args.protocol_fingerprint,
            "schedule_index": args.schedule_index,
            "expected_agent_source_hash": args.expected_agent_source_hash,
        }
        if external_command is not None:
            spec["external_command"] = external_command
        if replay_source is not None:
            spec["replay_source"] = str(replay_source)
        input_path = attempt.input
        write_json_atomically(input_path, spec)
        timeout_seconds = int(job["sandbox"]["timeout_seconds"])
        completed = run_with_deadline(
            [sys.executable, str(adapter.worker_path), "--input", str(input_path)],
            cwd=REGRESSION,
            env={**os.environ, "PYTHONPATH": str(REGRESSION / "src")},
            # The external worker owns the Trial deadline and cleans up its
            # own Agent process group.  Keep a small parent-only margin so it
            # can persist the terminal Attempt evidence before the hard stop.
            timeout_seconds=timeout_seconds + 5,
        )
        if completed.stdout:
            print(completed.stdout)
        if completed.stderr:
            print(completed.stderr, file=sys.stderr)
        result_path = attempt.result
        if completed.timed_out:
            result = _timed_out_result(job, timeout_seconds)
            result["trace_path"] = str(attempt.trace)
            result["attempt_id"] = attempt.attempt_id
            write_json_atomically(result_path, result)
        else:
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                result = {
                    **_timed_out_result(job, timeout_seconds),
                    "status": "infra_failed",
                    "error": f"worker exited without a valid result: {type(exc).__name__}: {exc}",
                    "trace_path": str(attempt.trace),
                    "attempt_id": attempt.attempt_id,
                }
                write_json_atomically(result_path, result)
        attempts.finish_attempt(attempt, _attempt_status(result), error=result.get("error") if isinstance(result.get("error"), str) else None)
        if args.protocol_fingerprint:
            # This identity is injected by the platform rather than accepted
            # from an Adapter or external Agent output.
            result["protocol_fingerprint"] = args.protocol_fingerprint
        if external_command is not None:
            # The Worker measures this independently before running the Agent;
            # retain a runner-side value even if the Worker crashes early.
            result.setdefault("agent_source_hash", _external_command_source_hash(external_command))
            result["expected_agent_source_hash"] = args.expected_agent_source_hash
            result["agent_source_hash_matches_protocol"] = (
                isinstance(args.expected_agent_source_hash, str)
                and result.get("agent_source_hash") == args.expected_agent_source_hash
            )
        if args.schedule_index is not None:
            result["schedule_index"] = args.schedule_index
        result.setdefault("adapter_id", adapter.adapter_id)
        result.setdefault("adapter_capabilities", adapter_capabilities.as_dict())
        result["behavior"] = summarize_trial_behavior(result)
        # The Adapter owns its raw result; the Runner owns protocol identity.
        # Persist the enriched version before ranking attempts so an Attempt
        # and its published Job result carry the same frozen identity.
        write_json_atomically(attempt.result, result)
        # A newly completed physical execution changes the Trial projection.
        # Existing projections are only reused during read/resume; they never
        # suppress publication of a later terminal Attempt.
        selected = attempts.select_latest_terminal_attempt()
        if selected is None:
            selected_result = result
        else:
            selected_attempt_paths, selected_result = selected
            _write_selected_result(job_dir, selected_attempt_paths, selected_result)
        selected_attempt = attempts.resolve_selected_attempt()
        if selected_attempt is not None:
            _sync_selected_store(run_store, attempts, selected_attempt[0], selected_attempt[1])
        summaries.append(_job_summary(job, selected_result))
        attempts.release_trial_lock()

    summary_path = output_dir / "summary.json"
    prior_jobs: dict[str, dict[str, object]] = {}
    if summary_path.exists():
        try:
            prior = json.loads(summary_path.read_text(encoding="utf-8"))
            prior_jobs = {str(item.get("job_id")): item for item in prior.get("jobs", []) if isinstance(item, dict) and isinstance(item.get("job_id"), str)}
        except (OSError, json.JSONDecodeError):
            prior_jobs = {}
    prior_jobs.update({str(item["job_id"]): item for item in summaries})
    merged_jobs = sorted(prior_jobs.values(), key=lambda item: (int(item.get("trial_index", 0)), str(item.get("job_id", ""))))
    summary = {"manifest": manifest["id"], "job_count": len(merged_jobs), "jobs": merged_jobs}
    write_json_atomically(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(job["status"] == "completed" and job["evaluation_passed"] for job in summaries) else 1


def _job_summary(job: dict[str, object], result: dict[str, object]) -> dict[str, object]:
    scores = {score.get("evaluator"): score for score in result.get("scores", []) if isinstance(score, dict)}
    diff_violations = (scores.get("diff", {}).get("actual", {}) or {}).get("violations", [])
    actual_diff_policy_violation = any(
        isinstance(violation, str) and violation != "empty_diff"
        for violation in diff_violations
    )
    capabilities = AdapterCapabilities.from_snapshot(result.get("adapter_capabilities"))
    behavior = result.get("behavior") if isinstance(result.get("behavior"), dict) else summarize_trial_behavior(result)
    # 外部 black-box 仍会有平台生命周期 Trace，但这不能伪装成工具或模型证据。
    tool_calls = scores.get("tool_integrity", {}).get("actual", {}).get("tool_calls", 0)
    model_tokens = result.get("model_usage", {}).get("total_tokens", 0)
    if capabilities is not None and not capabilities.tool_trace:
        tool_calls = None
    if capabilities is not None and not capabilities.model_usage:
        model_tokens = None
    return {
        "job_id": job["job_id"],
        "case_id": job["case_id"],
        "trial_index": job["trial_index"],
        "status": result.get("status"),
        "evaluation_passed": result.get("evaluation", {}).get("passed", False),
        "trace_valid": (result.get("trace_validation") or {}).get("valid"),
        "test_passed": scores.get("test", {}).get("passed", False),
        "path_policy_passed": scores.get("path_policy", {}).get("passed"),
        # ``empty_diff`` is an expected downstream symptom of a model/infra
        # failure, not a policy breach. Other DiffEvaluator violations retain
        # their policy meaning and are eligible for the Gate rate.
        "diff_policy_violated": actual_diff_policy_violation,
        "tool_calls": tool_calls,
        "duration_ms": scores.get("budget", {}).get("actual", {}).get("duration_ms", 0),
        "added_lines": scores.get("diff", {}).get("actual", {}).get("added_lines", 0),
        "deleted_lines": scores.get("diff", {}).get("actual", {}).get("deleted_lines", 0),
        "model_tokens": model_tokens,
        "adapter_id": result.get("adapter_id"),
        "adapter_capabilities": result.get("adapter_capabilities"),
        "behavior": behavior,
        "behavior_snapshot": snapshot_trial_behavior(result),
        "failure_attribution": result.get("failure_attribution") or attribute_trial(result),
        "error": result.get("error"),
        "trace_id": result.get("trace_id"),
        "agent_source_hash": result.get("agent_source_hash"),
        "expected_agent_source_hash": result.get("expected_agent_source_hash"),
        "agent_source_hash_matches_protocol": result.get("agent_source_hash_matches_protocol"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
