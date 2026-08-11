#!/usr/bin/env python3
"""Run one real-model, worktree-scoped ReAct Trial."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from adapters.react_agent.model_client import ModelClientError, OpenAICompatibleClient
from adapters.react_agent.tools import ToolExecutor, openai_tools, resolve_tool_policy
from regression_lab.evaluators import evaluate_baseline
from regression_lab.sandbox import DockerSandbox, SandboxConfig, SandboxUnavailable
from regression_lab.schema import validate_trace
from regression_lab.store import RunStore
from regression_lab.trace import TraceCollector


def _agent_profile(agent_version: Any, test_command: str) -> tuple[str, str]:
    """Return the versioned operating policy used in an experiment."""

    version = str(agent_version or "react-agent-v1")
    base = (
        "You are a coding agent. Modify only the supplied worktree. "
        "Use tools to inspect and repair the code, then reply briefly when finished."
    )
    if version.endswith("-v2"):
        return (
            "verify-once-v2",
            base + " Work efficiently: first inspect the failing test and the directly relevant source files; "
            "then make one minimal implementation-only change. After a successful edit, run this exact verification "
            f"command once: {test_command!r}. Do not repeat glob/read/test calls unless a tool reports an error or the test fails. "
            "If the test fails, inspect its output before making one recovery edit.",
        )
    return "react-basic-v1", base


def _git(worktree: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError((completed.stdout + completed.stderr).strip())
    return completed.stdout


def _run_test(worktree: Path, command: str, sandbox: DockerSandbox | None, timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if sandbox:
            value = sandbox.run(command, timeout_seconds=timeout)
            return {"exit_code": value.exit_code, "duration_ms": value.duration_ms, "stdout": value.stdout, "stderr": value.stderr, "sandbox_status": value.status}
        process = subprocess.run(command.split(), cwd=worktree, text=True, capture_output=True, timeout=timeout)
        return {"exit_code": process.returncode, "duration_ms": round((time.monotonic() - started) * 1000, 3), "stdout": process.stdout, "stderr": process.stderr}
    except SandboxUnavailable as exc:
        return {"exit_code": -2, "duration_ms": 0, "stdout": "", "stderr": f"sandbox unavailable: {exc}", "sandbox_status": "unavailable"}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "duration_ms": round((time.monotonic() - started) * 1000, 3), "stdout": "", "stderr": "test command timed out", "sandbox_status": "timed_out"}


def _record_git_evidence(result: dict[str, Any], worktree: Path) -> None:
    _git(worktree, "add", "-N", "--", ".")
    status = _git(worktree, "status", "--porcelain")
    result["changed_files"] = [line[3:] for line in status.splitlines() if line.strip()]
    result["git_diff"] = _git(worktree, "diff", "HEAD", "--no-ext-diff", "--binary")
    result["git_evidence"] = {"base_revision": _git(worktree, "rev-parse", "HEAD").strip(), "status_porcelain": status, "diff_base": "HEAD", "captures_untracked": True}


def run_trial(spec: dict[str, Any], client: OpenAICompatibleClient | None = None) -> dict[str, Any]:
    trial_id, worktree = str(spec["trial_id"]), Path(spec["worktree"]).resolve()
    trace = TraceCollector(Path(spec["trace_output"]), f"trace_{uuid.uuid4().hex[:12]}")
    profile_id, system_prompt = _agent_profile(spec.get("agent_version"), str(spec["test_command"]))
    root = trace.start_span("agent.run", trial_id=trial_id, agent_version=spec.get("agent_version"), adapter_id="react-agent", agent_profile=profile_id, case_id=spec.get("case_id"))
    started = time.monotonic()
    result: dict[str, Any] = {"trial_id": trial_id, "adapter_id": "react-agent", "adapter_version": (spec.get("adapter") or {}).get("default_version", "react-agent-v1"), "agent_version": spec.get("agent_version"), "agent_profile": profile_id, "status": "infra_failed", "trace_id": trace.trace_id, "agent_exit_reason": None, "agent_response": "", "changed_files": [], "test_exit_code": None, "error": None, "allowed_paths": spec.get("allowed_paths", ["**"]), "forbidden_paths": spec.get("forbidden_paths", []), "allowed_tools": [], "denied_tools": [], "budget": spec.get("budget", {}), "trace_path": str(spec["trace_output"]), "run_store": spec.get("run_store"), "model_usage": {}}
    try:
        if not worktree.is_dir():
            raise ValueError(f"worktree does not exist: {worktree}")
        allowed, denied = resolve_tool_policy(spec)
        result["allowed_tools"], result["denied_tools"] = sorted(allowed), sorted(denied)
        sandbox_spec = spec.get("sandbox")
        sandbox = DockerSandbox(worktree, SandboxConfig(**{key: value for key, value in sandbox_spec.items() if key in SandboxConfig.__dataclass_fields__})) if sandbox_spec else None
        executor = ToolExecutor(
            worktree,
            sandbox,
            allowed_paths=tuple(result["allowed_paths"]),
            forbidden_paths=tuple(result["forbidden_paths"]),
        )
        client = client or OpenAICompatibleClient.from_environment()
        max_calls, max_tokens = int(result["budget"].get("max_tool_calls", 20)), int(spec.get("max_tokens", 1000))
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": str(spec["prompt"])},
        ]
        calls = 0
        while True:
            model_span = trace.start_span("model.call", parent_id=root, model=client.model, message_count=len(messages), tool_count=len(allowed), max_tokens=max_tokens)
            model_started = time.monotonic()
            try:
                reply = client.complete(messages, openai_tools(allowed), max_tokens)
                trace.end_span(model_span, status="ok", duration_ms=round((time.monotonic() - model_started) * 1000, 3), finish_reason=reply.finish_reason, tool_call_count=len(reply.tool_calls), usage=reply.usage)
            except ModelClientError as exc:
                trace.end_span(model_span, status="error", duration_ms=round((time.monotonic() - model_started) * 1000, 3), error_type=type(exc).__name__)
                raise
            result["model_usage"] = {key: result["model_usage"].get(key, 0) + value for key, value in reply.usage.items()}
            if not reply.tool_calls:
                result["agent_response"], result["agent_exit_reason"], result["status"] = reply.text, "model_completed", "completed"
                break
            messages.append({"role": "assistant", "content": reply.text or None, "tool_calls": [{"id": item.call_id, "type": "function", "function": {"name": item.name, "arguments": json.dumps(item.arguments, ensure_ascii=False)}} for item in reply.tool_calls]})
            for call in reply.tool_calls:
                calls += 1
                if calls > max_calls:
                    result["status"], result["agent_exit_reason"] = "budget_exceeded", "max_tool_calls"
                    raise StopIteration
                span = trace.start_span("tool.call", parent_id=root, tool_name=call.name, tool_use_id=call.call_id)
                tool_started = time.monotonic()
                try:
                    if call.name not in allowed:
                        raise PermissionError("not_allowed_by_trial_policy")
                    output = executor.execute(call.name, call.arguments)
                    trace.end_span(span, status="ok", duration_ms=round((time.monotonic() - tool_started) * 1000, 3), output_preview=output[:240])
                except Exception as exc:
                    output = f"Error: {type(exc).__name__}: {exc}"
                    trace.end_span(span, status="denied" if isinstance(exc, PermissionError) else "error", duration_ms=round((time.monotonic() - tool_started) * 1000, 3), error_type=type(exc).__name__)
                messages.append({"role": "tool", "tool_call_id": call.call_id, "content": output})
        if result["status"] == "completed":
            test = _run_test(worktree, str(spec["test_command"]), sandbox, sandbox.config.timeout_seconds if sandbox else 30)
            result.update({"test_exit_code": test["exit_code"], "test_duration_ms": test["duration_ms"], "test_stdout": test["stdout"], "test_stderr": test["stderr"]})
            result["status"] = "infra_failed" if test.get("sandbox_status") == "unavailable" else "timed_out" if test.get("sandbox_status") == "timed_out" else "completed" if test["exit_code"] == 0 else "agent_failed"
    except StopIteration:
        pass
    except ModelClientError as exc:
        result.update({"status": "model_failed", "error": str(exc), "agent_exit_reason": "model_error"})
        trace.event("error", parent_id=root, error_type="model_failed")
    except Exception as exc:
        result.update({"status": "infra_failed", "error": f"{type(exc).__name__}: {exc}"})
        trace.event("error", parent_id=root, error_type=type(exc).__name__)
    finally:
        try:
            _record_git_evidence(result, worktree)
        except Exception as exc:
            result["status"], result["error"] = "infra_failed", f"git evidence failed: {type(exc).__name__}: {exc}"
        trace.end_span(root, status="ok" if result["status"] == "completed" else result["status"], duration_ms=round((time.monotonic() - started) * 1000, 3))
        result["trace_validation"] = validate_trace(result["trace_path"], expected_trace_id=trace.trace_id, expected_trial_id=trial_id).as_dict()
        if result["status"] == "completed" and not result["trace_validation"]["valid"]:
            # A passing test without a valid evidence trace is not a completed,
            # resumable Trial. This mirrors the replay adapter's fail-closed
            # trace_incomplete state.
            result["status"] = "trace_incomplete"
            result["error"] = "trace validation failed"
        result["trace_summary"] = trace.summary()
        result["evaluation"] = evaluate_baseline(result)
        result["scores"] = result["evaluation"]["scores"]
        if result.get("run_store"):
            try: RunStore(result["run_store"]).record_run(result, result["scores"])
            except Exception as exc: result["store_error"] = f"{type(exc).__name__}: {exc}"
        Path(spec["result_output"]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--input", required=True); args = parser.parse_args()
    result = run_trial(json.loads(Path(args.input).read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
