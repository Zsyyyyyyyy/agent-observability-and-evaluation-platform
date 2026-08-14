#!/usr/bin/env python3
"""Execute one user-approved local Agent command under the Regression contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from regression_lab.evaluators import evaluate_baseline
from regression_lab.behavior import summarize_trial_behavior
from regression_lab.attribution import attribute_trial
from regression_lab.runner import terminate_process_group
from regression_lab.sandbox import DockerSandbox, SandboxConfig, SandboxUnavailable
from regression_lab.schema import validate_trace
from regression_lab.store import RunStore
from regression_lab.artifacts import write_json_atomically


def _git(worktree: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError((completed.stdout + completed.stderr).strip())
    return completed.stdout


def _record_git_evidence(result: dict[str, Any], worktree: Path) -> None:
    _git(worktree, "add", "-N", "--", ".")
    status = _git(worktree, "status", "--porcelain")
    result["changed_files"] = [line[3:] for line in status.splitlines() if line.strip()]
    result["git_diff"] = _git(worktree, "diff", "HEAD", "--no-ext-diff", "--binary")
    result["git_evidence"] = {"base_revision": _git(worktree, "rev-parse", "HEAD").strip(), "status_porcelain": status, "diff_base": "HEAD", "captures_untracked": True}


def _run_test(worktree: Path, command: str, sandbox: DockerSandbox | None, timeout: int,
              environment: dict[str, str]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if sandbox:
            value = sandbox.run(command, timeout_seconds=timeout)
            return {"exit_code": value.exit_code, "duration_ms": value.duration_ms, "stdout": value.stdout, "stderr": value.stderr, "sandbox_status": value.status}
        process = subprocess.run(shlex.split(command), cwd=worktree, env=environment, text=True, capture_output=True, timeout=timeout)
        return {"exit_code": process.returncode, "duration_ms": round((time.monotonic() - started) * 1000, 3), "stdout": process.stdout, "stderr": process.stderr}
    except SandboxUnavailable as exc:
        return {"exit_code": -2, "duration_ms": 0, "stdout": "", "stderr": f"sandbox unavailable: {exc}", "sandbox_status": "unavailable"}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "duration_ms": round((time.monotonic() - started) * 1000, 3), "stdout": "", "stderr": "test command timed out", "sandbox_status": "timed_out"}


def _run_external_command(command: list[str], *, worktree: Path, environment: dict[str, str], timeout: int) -> tuple[int, str, str, bool]:
    """Run an external Agent in an isolated process group with bounded cleanup."""

    process = subprocess.Popen(
        command,
        cwd=worktree,
        env=environment,
        text=True,
        shell=False,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return process.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        terminate_process_group(process)
        stdout, stderr = process.communicate()
        return process.returncode if process.returncode is not None else -1, stdout, stderr, True


MODEL_FAILURE_KINDS = frozenset({
    "configuration", "http_429", "http_4xx", "http_5xx", "http_other",
    "timeout", "network", "invalid_response", "invalid_tool_call", "unknown",
})


def _command_source_hash(command: list[str]) -> str | None:
    """Measure the trusted local entry point before it is executed."""

    candidate = Path(command[-1])
    if not candidate.is_file():
        return None
    return "sha256:" + hashlib.sha256(candidate.read_bytes()).hexdigest()


def _read_output(path: Path) -> tuple[str, str, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid agent output: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid agent output: expected object")
    response, reason = payload.get("agent_response"), payload.get("agent_exit_reason")
    if not isinstance(response, str) or not isinstance(reason, str):
        raise ValueError("invalid agent output: agent_response and agent_exit_reason must be strings")
    if len(response) > 4096 or len(reason) > 128:
        raise ValueError("invalid agent output: field exceeds contract limit")
    failure_kind = payload.get("model_failure_kind")
    if failure_kind is not None and (reason != "model_error" or failure_kind not in MODEL_FAILURE_KINDS):
        raise ValueError("invalid agent output: unsupported model_failure_kind")
    return response, reason, failure_kind


def _model_usage(trace_path: Path) -> dict[str, int]:
    """Aggregate usage only from the validated trace, never Agent output."""
    totals: dict[str, int] = {}
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return totals
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("kind") != "span_end":
            continue
        usage = (event.get("attributes") or {}).get("usage")
        if not isinstance(usage, dict):
            continue
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                totals[key] = totals.get(key, 0) + value
    return totals


def _root_profile(trace_path: Path) -> str | None:
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("kind") == "span_start" and event.get("name") == "agent.run":
            value = (event.get("attributes") or {}).get("agent_profile")
            return value if isinstance(value, str) else None
    return None


def _trace_model_failure_kind(trace_path: Path) -> str | None:
    """Read only a validated, allow-listed diagnostic category from Trace."""

    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        attrs = event.get("attributes") or {}
        value = attrs.get("model_failure_kind") if isinstance(attrs, dict) else None
        if value in MODEL_FAILURE_KINDS:
            return value
    return None


def run_trial(spec: dict[str, Any]) -> dict[str, Any]:
    trial_id, worktree = str(spec["trial_id"]), Path(spec["worktree"]).resolve()
    command = spec.get("external_command")
    trace_id = f"trace_{uuid.uuid4().hex[:12]}"
    trace_path, result_path = Path(spec["trace_output"]), Path(spec["result_output"])
    agent_output = result_path.with_name("agent-output.json")
    result: dict[str, Any] = {
        "trial_id": trial_id, "adapter_id": "external-command", "adapter_version": (spec.get("adapter") or {}).get("default_version", "external-agent-v1"),
        "agent_version": spec.get("agent_version"), "agent_profile": spec.get("agent_profile"), "attempt_id": spec.get("attempt_id"), "status": "infra_failed", "trace_id": trace_id,
        "agent_exit_reason": None, "agent_response": "", "model_failure": None, "changed_files": [], "test_exit_code": None, "error": None,
        "allowed_paths": spec.get("allowed_paths", ["**"]), "forbidden_paths": spec.get("forbidden_paths", []),
        "allowed_tools": spec.get("allowed_tools", []), "denied_tools": spec.get("denied_tools", []), "budget": spec.get("budget", {}),
        "trace_path": str(trace_path), "run_store": spec.get("run_store"), "model_usage": {},
        "agent_source_hash": None,
        "expected_agent_source_hash": spec.get("expected_agent_source_hash"),
        "agent_source_hash_matches_protocol": None,
    }
    try:
        if not worktree.is_dir():
            raise ValueError(f"worktree does not exist: {worktree}")
        if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
            raise ValueError("external_command must be a non-empty argv string array")
        result["agent_source_hash"] = _command_source_hash(command)
        expected_hash = result.get("expected_agent_source_hash")
        result["agent_source_hash_matches_protocol"] = (
            isinstance(expected_hash, str) and result["agent_source_hash"] == expected_hash
        )
        sandbox_spec = spec.get("sandbox")
        sandbox = DockerSandbox(worktree, SandboxConfig(**{key: value for key, value in sandbox_spec.items() if key in SandboxConfig.__dataclass_fields__})) if sandbox_spec else None
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT / "src"), os.environ.get("PYTHONPATH", "")])),
            "REGRESSION_TRIAL_ID": trial_id, "REGRESSION_TRACE_ID": trace_id, "REGRESSION_TRACE_PATH": str(trace_path),
            "REGRESSION_AGENT_OUTPUT_PATH": str(agent_output), "REGRESSION_WORKTREE": str(worktree), "REGRESSION_CASE_ID": str(spec.get("case_id", "")),
            "REGRESSION_AGENT_VERSION": str(spec.get("agent_version", "")), "REGRESSION_ADAPTER_ID": "external-command"}
        environment["REGRESSION_PROMPT"] = str(spec.get("prompt", ""))
        environment["REGRESSION_TEST_COMMAND"] = str(spec.get("test_command", ""))
        environment["REGRESSION_ALLOWED_TOOLS"] = json.dumps(spec.get("allowed_tools", []))
        environment["REGRESSION_ALLOWED_PATHS"] = json.dumps(spec.get("allowed_paths", []))
        environment["REGRESSION_FORBIDDEN_PATHS"] = json.dumps(spec.get("forbidden_paths", []))
        environment["REGRESSION_MAX_TOOL_CALLS"] = str((spec.get("budget") or {}).get("max_tool_calls", 20))
        environment["REGRESSION_MAX_TOKENS"] = str(spec.get("max_tokens", 1000))
        if spec.get("agent_profile"):
            environment["REGRESSION_AGENT_PROFILE"] = str(spec["agent_profile"])
        timeout = int((spec.get("sandbox") or {}).get("timeout_seconds", 30))
        try:
            returncode, stdout, stderr, timed_out = _run_external_command(
                command, worktree=worktree, environment=environment, timeout=timeout
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"external command unavailable: {exc.filename}") from exc
        if timed_out:
            result.update({"status": "timed_out", "error": "external command exceeded trial deadline", "agent_exit_reason": "deadline_exceeded"})
        else:
            result["agent_stdout"] = stdout[-2000:]
            result["agent_stderr"] = stderr[-2000:]
            if returncode != 0:
                result.update({"status": "agent_failed", "error": f"external command exited {returncode}", "agent_exit_reason": "process_error"})
            else:
                try:
                    result["agent_response"], result["agent_exit_reason"], failure_kind = _read_output(agent_output)
                except ValueError as exc:
                    result.update({"status": "agent_failed", "error": str(exc), "agent_exit_reason": "contract_error"})
                else:
                    if result["agent_exit_reason"] == "model_error":
                        result.update({
                            "status": "model_failed", "error": "external Agent reported a model error",
                            "model_failure": {"kind": failure_kind or "unknown"},
                        })
                    else:
                        test = _run_test(worktree, str(spec["test_command"]), sandbox, timeout, environment)
                        result.update({"test_exit_code": test["exit_code"], "test_duration_ms": test["duration_ms"], "test_stdout": test["stdout"], "test_stderr": test["stderr"]})
                        result["status"] = "infra_failed" if test.get("sandbox_status") == "unavailable" else "timed_out" if test.get("sandbox_status") == "timed_out" else "completed" if test["exit_code"] == 0 else "agent_failed"
    except ValueError as exc:
        result.update({"status": "agent_failed", "error": str(exc), "agent_exit_reason": "contract_error"})
    except Exception as exc:
        result.update({"status": "infra_failed", "error": f"{type(exc).__name__}: {exc}", "agent_exit_reason": "adapter_error"})
    finally:
        try:
            _record_git_evidence(result, worktree)
        except Exception as exc:
            result["status"], result["error"] = "infra_failed", f"git evidence failed: {type(exc).__name__}: {exc}"
        result["trace_validation"] = validate_trace(trace_path, expected_trace_id=trace_id, expected_trial_id=trial_id, expected_root_attributes={"agent_version": str(spec.get("agent_version", "")), "adapter_id": "external-command"}).as_dict()
        if result["trace_validation"]["valid"]:
            result["model_usage"] = _model_usage(trace_path)
            result["agent_profile"] = _root_profile(trace_path) or result.get("agent_profile")
            if result["status"] == "model_failed":
                trace_kind = _trace_model_failure_kind(trace_path)
                if trace_kind:
                    result["model_failure"] = {"kind": trace_kind}
        result["behavior"] = summarize_trial_behavior(result)
        if result["status"] == "completed" and not result["trace_validation"]["valid"]:
            result.update({"status": "trace_incomplete", "error": "trace validation failed"})
        result["evaluation"] = evaluate_baseline(result)
        result["scores"] = result["evaluation"]["scores"]
        result["failure_attribution"] = attribute_trial(result)
        if result.get("run_store"):
            try:
                RunStore(result["run_store"]).record_run(result, result["scores"])
            except Exception as exc:
                result["store_error"] = f"{type(exc).__name__}: {exc}"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomically(result_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--input", required=True); args = parser.parse_args()
    result = run_trial(json.loads(Path(args.input).read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
