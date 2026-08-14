#!/usr/bin/env python3
"""Run one external Agent Trial with replayed model responses.

Day 2 deliberately keeps this worker dependency-light. It loads the existing
external Agent module without editing it, replaces only its runtime boundaries, and
produces a JSONL trace plus a structured result. Real model and Docker-backed
handlers are follow-up work after this smoke path is proven.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from regression_lab.trace import TraceCollector
from regression_lab.sandbox import DockerSandbox, SandboxConfig, SandboxUnavailable
from regression_lab.schema import validate_trace
from regression_lab.store import RunStore
from regression_lab.artifacts import write_json_atomically
from regression_lab.evaluators import evaluate_baseline


SUPPORTED_TOOLS = frozenset({"read_file", "write_file", "edit_file", "glob", "bash"})
ALWAYS_DENIED_TOOLS = frozenset({
    "spawn_teammate", "connect_mcp", "schedule_cron", "create_worktree",
    "remove_worktree", "keep_worktree",
})


def resolve_tool_policy(spec: dict[str, Any]) -> tuple[frozenset[str], frozenset[str]]:
    """Build the effective tool set from the Trial spec, fail-closed on mismatch."""

    requested = spec.get("allowed_tools", sorted(SUPPORTED_TOOLS))
    denied = spec.get("denied_tools", [])
    if not isinstance(requested, list) or not all(isinstance(tool, str) for tool in requested):
        raise ValueError("allowed_tools must be a string list")
    if not isinstance(denied, list) or not all(isinstance(tool, str) for tool in denied):
        raise ValueError("denied_tools must be a string list")
    requested_set = frozenset(requested)
    denied_set = frozenset(denied)
    unsupported = requested_set - SUPPORTED_TOOLS
    forbidden = requested_set & ALWAYS_DENIED_TOOLS
    if unsupported:
        raise ValueError(f"allowed_tools contains unsupported tools: {sorted(unsupported)}")
    if forbidden:
        raise ValueError(f"allowed_tools contains permanently denied tools: {sorted(forbidden)}")
    return requested_set - denied_set, denied_set | ALWAYS_DENIED_TOOLS


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ReplayResponse:
    content: list[Any]
    stop_reason: str


def _install_import_stubs() -> None:
    """Allow replay smoke tests to run before optional project deps install."""

    if "anthropic" not in sys.modules:
        anthropic = types.ModuleType("anthropic")

        class Anthropic:
            def __init__(self, *args: Any, **kwargs: Any):
                self.messages = types.SimpleNamespace(create=None)

        anthropic.Anthropic = Anthropic
        sys.modules["anthropic"] = anthropic

    if "dotenv" not in sys.modules:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda *args, **kwargs: None
        sys.modules["dotenv"] = dotenv

    if "yaml" not in sys.modules:
        yaml = types.ModuleType("yaml")
        yaml.YAMLError = Exception
        yaml.safe_load = lambda text: {}
        sys.modules["yaml"] = yaml


class ReplayClient:
    """Deterministic model replay with an optional Docker-backed bash turn."""

    def __init__(self, include_bash: bool = False, case_id: str = ""):
        self.calls = 0
        self.include_bash = include_bash
        self.case_id = case_id

    def create(self, **kwargs: Any) -> ReplayResponse:
        self.calls += 1
        if self.calls == 1:
            path = "src/normalizer.py" if self.case_id == "normalize_none_input" else "src/calculator.py"
            return ReplayResponse(
                content=[ToolUse("tool_001", "read_file", {"path": path})],
                stop_reason="tool_use",
            )
        if self.include_bash and self.calls == 2:
            return ReplayResponse(
                content=[ToolUse(
                    "tool_bash_001",
                    "bash",
                    {"command": "python -c 'print(\"sandbox-ok\")'"},
                )],
                stop_reason="tool_use",
            )
        edit_call = 3 if self.include_bash else 2
        if self.calls == edit_call:
            if self.case_id == "normalize_none_input":
                old = "def normalize_name(value):\n    return value.strip().lower()"
                new = (
                    "def normalize_name(value):\n"
                    "    if value is None:\n"
                    "        return \"\"\n"
                    "    return value.strip().lower()"
                )
                path = "src/normalizer.py"
            else:
                old = "def calculate(value):\n    return int(value) + 1"
                new = (
                    "def calculate(value):\n"
                    "    if value == \"\":\n"
                    "        return 0\n"
                    "    return int(value) + 1"
                )
                path = "src/calculator.py"
            return ReplayResponse(
                content=[ToolUse(
                    "tool_002",
                    "edit_file",
                    {"path": path, "old_text": old, "new_text": new},
                )],
                stop_reason="tool_use",
            )
        return ReplayResponse(
            content=[TextBlock("修复完成，已运行测试。")],
            stop_reason="end_turn",
        )


def _load_replay_source(source: Path, worktree: Path):
    os.chdir(worktree)
    # The replay worker does not call a provider, but external Agent reads MODEL_ID at
    # import time. Real runs will override this through the Trial environment.
    os.environ.setdefault("MODEL_ID", "replay-model")
    _install_import_stubs()
    spec = importlib.util.spec_from_file_location("regression_readonly_replay_worker", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load external Agent source: {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _replace_tool_pool(
    module,
    collector: TraceCollector,
    allowed_tools: frozenset[str],
    sandbox: DockerSandbox | None = None,
    parent_span_id: str | None = None,
):
    original_handlers = dict(module.BUILTIN_HANDLERS)
    original_tools = list(module.BUILTIN_TOOLS)
    module.BUILTIN_TOOLS = [tool for tool in original_tools if tool["name"] in allowed_tools]
    module.PROMPT_SECTIONS["tools"] = (
        "Available tools for this benchmark: " + ", ".join(sorted(allowed_tools))
    )

    wrapped: dict[str, Any] = {}
    for name in allowed_tools:
        if name not in original_handlers:
            continue
        handler = original_handlers[name]

        def invoke(*args: Any, _name=name, _handler=handler, **kwargs: Any):
            span = collector.start_span("tool.call", parent_id=parent_span_id, tool_name=_name, mode="replay_smoke")
            started = time.monotonic()
            try:
                if _name == "bash":
                    if sandbox is None:
                        output = "Denied: Docker Tool Sandbox is required for bash"
                        collector.end_span(
                            span,
                            status="denied",
                            duration_ms=round((time.monotonic() - started) * 1000, 3),
                            sandbox="not_configured",
                        )
                        return output
                    command = kwargs.get("command", "")
                    sandbox_result = sandbox.run(command)
                    output = (sandbox_result.stdout + sandbox_result.stderr).strip()
                    collector.end_span(
                        span,
                        status="ok" if sandbox_result.status == "completed" else sandbox_result.status,
                        duration_ms=round((time.monotonic() - started) * 1000, 3),
                        exit_code=sandbox_result.exit_code,
                        sandbox_status=sandbox_result.status,
                    )
                    return output or "(no output)"
                else:
                    output = _handler(*args, **kwargs)
                collector.end_span(
                    span,
                    status="ok" if not str(output).startswith("Error:") else "error",
                    duration_ms=round((time.monotonic() - started) * 1000, 3),
                    output_preview=str(output)[:240],
                )
                return output
            except Exception as exc:
                collector.end_span(
                    span,
                    status="error",
                    duration_ms=round((time.monotonic() - started) * 1000, 3),
                    error_type=type(exc).__name__,
                )
                raise

        wrapped[name] = invoke

    for denied in set(original_handlers) - set(allowed_tools):
        def deny(*args: Any, _name=denied, **kwargs: Any):
            span = collector.start_span("tool.call", parent_id=parent_span_id, tool_name=_name, mode="policy_denied")
            started = time.monotonic()
            collector.event("permission.check", parent_id=span, tool_name=_name, decision="denied")
            collector.end_span(
                span,
                status="denied",
                duration_ms=round((time.monotonic() - started) * 1000, 3),
                reason="not_allowed_by_trial_policy",
            )
            return f"Permission denied by Regression Tool Policy: {_name}"

        wrapped[denied] = deny

    module.BUILTIN_HANDLERS = wrapped


def _install_trace_wrappers(module, collector: TraceCollector, replay_client: ReplayClient, parent_span_id: str):
    module.client = types.SimpleNamespace(messages=types.SimpleNamespace(create=replay_client.create))

    original_call_llm = module.call_llm

    def traced_call_llm(messages, context, tools, state, max_tokens):
        span = collector.start_span(
            "model.call",
            parent_id=parent_span_id,
            model=state.current_model,
            message_count=len(messages),
            tool_count=len(tools),
            max_tokens=max_tokens,
        )
        started = time.monotonic()
        try:
            response = original_call_llm(messages, context, tools, state, max_tokens)
            collector.end_span(
                span,
                status="ok",
                duration_ms=round((time.monotonic() - started) * 1000, 3),
                stop_reason=getattr(response, "stop_reason", None),
                response_blocks=len(getattr(response, "content", []) or []),
                replay=True,
            )
            return response
        except Exception as exc:
            collector.end_span(
                span,
                status="error",
                duration_ms=round((time.monotonic() - started) * 1000, 3),
                error_type=type(exc).__name__,
                error=str(exc)[:240],
            )
            raise

    module.call_llm = traced_call_llm

    original_compact = module.compact_history

    def traced_compact(messages):
        collector.event("context.compact", parent_id=parent_span_id, strategy="explicit", message_count=len(messages))
        return original_compact(messages)

    module.compact_history = traced_compact

    original_reactive = module.reactive_compact

    def traced_reactive(messages):
        collector.event("context.compact", strategy="reactive", message_count=len(messages))
        return original_reactive(messages)

    module.reactive_compact = traced_reactive

    original_permission = module.permission_hook

    def traced_permission(block):
        result = original_permission(block)
        collector.event(
            "permission.check",
            tool_name=block.name,
            decision="denied" if result else "allowed",
            reason=str(result)[:240] if result else None,
        )
        return result

    module.permission_hook = traced_permission
    module.HOOKS["PreToolUse"] = [
        traced_permission if callback is original_permission else callback
        for callback in module.HOOKS["PreToolUse"]
    ]


def _run_test(
    worktree: Path,
    command: str,
    timeout_seconds: int = 30,
    sandbox: DockerSandbox | None = None,
) -> dict[str, Any]:
    if sandbox is not None:
        try:
            result = sandbox.run(command, timeout_seconds=timeout_seconds)
        except SandboxUnavailable as exc:
            return {
                "exit_code": -2,
                "duration_ms": 0,
                "stdout": "",
                "stderr": f"sandbox unavailable: {exc}",
                "sandbox_status": "unavailable",
            }
        return {
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "sandbox_status": result.status,
        }

    # Day 2 fallback runs a fixed, trusted smoke command on the host.
    argv = command.split()
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={
                **os.environ,
                "PYTHONPATH": str(worktree / "src"),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        return {
            "exit_code": completed.returncode,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": -1,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "stdout": str(exc.stdout or ""),
            "stderr": "test command timed out",
        }


def _git(worktree: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stdout + completed.stderr).strip())
    return completed.stdout


def run_trial(spec: dict[str, Any]) -> dict[str, Any]:
    trial_id = spec.get("trial_id", f"trial_{uuid.uuid4().hex[:10]}")
    worktree = Path(spec["worktree"]).resolve()
    source_value = spec.get("replay_source")
    if not source_value:
        raise ValueError("replay_source is required for the optional readonly-replay bridge")
    source = Path(str(source_value)).expanduser().resolve()
    trace_output = Path(spec["trace_output"]).resolve()
    result_output = Path(spec["result_output"]).resolve()
    if not worktree.is_dir():
        raise ValueError(f"worktree does not exist: {worktree}")
    if not source.is_file():
        raise ValueError(f"external Agent source does not exist: {source}")

    trace = TraceCollector(trace_output, f"trace_{uuid.uuid4().hex[:12]}")
    root_span = trace.start_span("agent.run", trial_id=trial_id, agent_version=spec.get("agent_version"))
    started = time.monotonic()
    result: dict[str, Any] = {
        "trial_id": trial_id,
        "adapter_id": spec.get("adapter_id", "readonly-replay"),
        "adapter_version": (spec.get("adapter") or {}).get("default_version", "readonly-replay-v1"),
        "attempt_id": spec.get("attempt_id"),
        "status": "infra_failed",
        "trace_id": trace.trace_id,
        "agent_exit_reason": None,
        "changed_files": [],
        "test_exit_code": None,
        "error": None,
        "allowed_paths": spec.get("allowed_paths", ["**"]),
        "forbidden_paths": spec.get("forbidden_paths", []),
        "allowed_tools": [],
        "denied_tools": [],
        "budget": spec.get("budget", {"max_tool_calls": 20, "max_duration_ms": 180000}),
        "trace_path": str(trace_output),
        "run_store": spec.get("run_store"),
    }
    module = None
    sandbox = None
    try:
        effective_tools, denied_tools = resolve_tool_policy(spec)
        result["allowed_tools"] = sorted(effective_tools)
        result["denied_tools"] = sorted(denied_tools)
        sandbox_spec = spec.get("sandbox")
        if sandbox_spec:
            config_fields = {
                key: value
                for key, value in sandbox_spec.items()
                if key in {"image", "network", "cpus", "memory", "pids_limit", "timeout_seconds", "tmpfs_size"}
            }
            sandbox = DockerSandbox(worktree, SandboxConfig(**config_fields))
        module = _load_replay_source(source, worktree)
        replay = ReplayClient(
            include_bash=bool(spec.get("replay_bash")),
            case_id=str(spec.get("case_id", "")),
        )
        _replace_tool_pool(module, trace, effective_tools, sandbox=sandbox, parent_span_id=root_span)
        _install_trace_wrappers(module, trace, replay, parent_span_id=root_span)
        messages = [{"role": "user", "content": spec["prompt"]}]
        context = module.update_context({}, messages)
        module.agent_loop(messages, context)

        last_text = ""
        for message in reversed(messages):
            if message.get("role") != "assistant":
                continue
            for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
                if getattr(block, "type", None) == "text":
                    last_text = getattr(block, "text", "")
                    break
            if last_text:
                break
        result["agent_exit_reason"] = "error_message" if "[Error]" in last_text else "stop_without_tool_use"
        result["agent_response"] = last_text

        test = _run_test(
            worktree,
            spec.get("test_command", "python -m unittest discover -s tests -v"),
            timeout_seconds=(sandbox.config.timeout_seconds if sandbox else 30),
            sandbox=sandbox,
        )
        result["test_exit_code"] = test["exit_code"]
        result["test_duration_ms"] = test["duration_ms"]
        result["test_stdout"] = test["stdout"]
        result["test_stderr"] = test["stderr"]
        if test.get("sandbox_status") == "unavailable":
            result["status"] = "infra_failed"
        elif test.get("sandbox_status") == "timed_out" or test["exit_code"] == -1:
            result["status"] = "timed_out"
        else:
            result["status"] = "completed" if test["exit_code"] == 0 else "agent_failed"
        _git(worktree, "add", "-N", "--", ".")
        status_porcelain = _git(worktree, "status", "--porcelain")
        result["changed_files"] = [
            line[3:] if len(line) > 3 else line
            for line in status_porcelain.splitlines()
            if line.strip()
        ]
        result["git_diff"] = _git(worktree, "diff", "HEAD", "--no-ext-diff", "--binary")
        result["git_evidence"] = {
            "base_revision": _git(worktree, "rev-parse", "HEAD").strip(),
            "status_porcelain": status_porcelain,
            "diff_base": "HEAD",
            "captures_untracked": True,
        }
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["status"] = "infra_failed"
        trace.event("error", error_type=type(exc).__name__, error=str(exc)[:500])
    finally:
        trace.end_span(root_span, status="ok" if result["status"] == "completed" else result["status"], duration_ms=round((time.monotonic() - started) * 1000, 3))
        validation = validate_trace(
            trace_output,
            expected_trace_id=trace.trace_id,
            expected_trial_id=str(trial_id),
        )
        result["trace_validation"] = validation.as_dict()
        if result["status"] == "completed" and not validation.valid:
            result["status"] = "trace_incomplete"
        result["trace_summary"] = trace.summary()
        result["evaluation"] = evaluate_baseline(result)
        result["scores"] = result["evaluation"]["scores"]
        run_store = spec.get("run_store")
        if run_store:
            try:
                store = RunStore(run_store)
                store.record_run(result, result["scores"])
            except Exception as exc:
                result["store_error"] = f"{type(exc).__name__}: {exc}"
        result_output.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomically(result_output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Trial JSON input")
    args = parser.parse_args()
    spec = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = run_trial(spec)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
