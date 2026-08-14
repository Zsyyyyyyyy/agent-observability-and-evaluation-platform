#!/usr/bin/env python3
"""Framework-neutral OpenAI-compatible Coding Agent reference implementation.

It consumes only the external-command environment contract.  All versions
share the same model, tools and loop; the system Prompt Profile is the sole
intervention.
"""

from __future__ import annotations

import json
import hashlib
import os
import shlex
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from regression_lab.openai_compatible import ModelClientError, OpenAICompatibleClient
from regression_lab.sdk import AgentObserver
from regression_lab.tool_semantics import semantic_tool_attributes


SCHEMAS = {
    "read_file": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
    "write_file": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False},
    "edit_file": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"], "additionalProperties": False},
    "glob": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"], "additionalProperties": False},
    "bash": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"], "additionalProperties": False},
}


# This is deliberately a fixed runtime intervention, not an instruction in
# the Prompt.  It makes the negative control a one-variable comparison with
# V3: same Prompt and normal tool loop, plus two model completions after V3
# would otherwise have finished.
NEGATIVE_CONTROL_REDUNDANT_COMPLETIONS = 2
# Preserve room in the *tool* loop to deterministically enter the negative
# control.  The two redundant model calls themselves never consume tools.
NEGATIVE_CONTROL_TOOL_CALL_RESERVE = 2


def profile(version: str, test_command: str) -> tuple[str, str]:
    base = "You are a coding agent. Work only in the supplied worktree. Use the available tools to inspect and repair the task. Do not modify tests."
    if version.endswith("-v3-negative"):
        # Keep the V3 Prompt byte-for-byte identical.  The negative-control
        # behavior is injected only after V3's normal terminal model reply.
        return "targeted-context-verify-v3-plus-two-redundant-completions", base + f" Start by locating the smallest relevant source and test context with one glob/read pass. Make one minimal edit, then verify once with the bash tool using exactly this command: {test_command}. Prefer the shortest correct tool path; only investigate further after a concrete error."
    if version.endswith("-v4.1"):
        return "bounded-success-stop-verify-v4-1", base + f" Start by locating the smallest relevant source and test context with one glob/read pass. Make one minimal edit, then verify once with the bash tool using exactly this command: {test_command}. Prefer the shortest correct tool path; only investigate further after a concrete error. The platform will end the run as soon as that exact verification command succeeds. If a tool call is denied, do not retry the same operation or inspect or modify the denied target; continue only within the allowed source paths. Never inspect or modify __pycache__, .pyc files, or other generated build artifacts."
    if version.endswith("-v4"):
        return "success-stop-verify-v4", base + f" Start by locating the smallest relevant source and test context with one glob/read pass. Make one minimal edit, then verify once with the bash tool using exactly this command: {test_command}. Prefer the shortest correct tool path; only investigate further after a concrete error. When that exact verification command reports success, stop immediately and return the final answer without calling another tool. If a tool call is denied, do not retry the same operation or inspect or modify the denied target; continue only within the allowed source paths. Never inspect or modify __pycache__, .pyc files, or other generated build artifacts."
    if version.endswith("-v3"):
        return "targeted-context-verify-v3", base + f" Start by locating the smallest relevant source and test context with one glob/read pass. Make one minimal edit, then verify once with the bash tool using exactly this command: {test_command}. Prefer the shortest correct tool path; only investigate further after a concrete error."
    if version.endswith("-v2"):
        return "observe-plan-act-verify-v2", base + f" First inspect the failing test and related source, plan one minimal change, then verify once using the bash tool with exactly this command: {test_command}. Do not repeat reads or verification unless an error occurs."
    return "direct-repair-v1", base + f" Inspect the task, make a correct minimal fix, then verify with the bash tool using this exact command: {test_command}."


def describe_protocol(payload: dict[str, Any]) -> dict[str, Any]:
    """Describe rendered Prompt identities without exposing Prompt text."""

    versions = payload.get("versions")
    test_commands = payload.get("test_commands")
    if not isinstance(versions, list) or not all(isinstance(item, str) and item for item in versions):
        raise ValueError("versions must be a non-empty string list")
    if not isinstance(test_commands, list) or not all(isinstance(item, str) for item in test_commands):
        raise ValueError("test_commands must be a string list")
    profiles: dict[str, dict[str, str]] = {}
    for version in versions:
        rendered = [profile(version, command) for command in sorted(test_commands)]
        profile_ids = {item[0] for item in rendered}
        if len(profile_ids) != 1:
            raise ValueError(f"profile id changes across Cases for {version}")
        canonical = json.dumps([item[1] for item in rendered], ensure_ascii=False, separators=(",", ":"))
        profiles[version] = {
            "profile_id": next(iter(profile_ids)),
            "rendered_prompt_set_hash": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        }
    return {"schema_version": 1, "profiles": profiles}


def _path(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("path must be a non-empty string")
    path = (root / value).resolve()
    if root not in path.parents and path != root:
        raise ValueError("path escapes worktree")
    return path


def execute_with_outcome(root: Path, name: str, args: dict[str, Any], allowed_paths: list[str], forbidden_paths: list[str], test_command: str) -> tuple[str, bool]:
    """Execute a tool and preserve whether the exact verification passed."""
    if name == "read_file": return _path(root, args.get("path")).read_text(encoding="utf-8"), False
    if name == "glob": return "\n".join(sorted(path.relative_to(root).as_posix() for path in root.glob(str(args.get("pattern", ""))) if path.is_file())), False
    if name == "bash":
        command = args.get("command")
        if not isinstance(command, str) or not _same_test_command(command, test_command):
            raise PermissionError("only the platform-provided test command is permitted")
        argv = shlex.split(command)
        if argv[0] in {"python", "python3", "python3.11"}:
            argv[0] = sys.executable
        completed = subprocess.run(argv, cwd=root, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=60, shell=False)
        output = (completed.stdout + completed.stderr).strip()
        return (output or f"exit_code={completed.returncode}")[:240], completed.returncode == 0
    if name == "write_file":
        path, content = _path(root, args.get("path")), args.get("content")
        if not isinstance(content, str): raise ValueError("content must be string")
        relative = path.relative_to(root).as_posix()
        if any(fnmatch(relative, pattern) for pattern in forbidden_paths) or not any(fnmatch(relative, pattern) for pattern in allowed_paths): raise PermissionError(f"path denied by Trial policy: {relative}")
        path.write_text(content, encoding="utf-8"); return f"wrote {path.relative_to(root)}", False
    if name == "edit_file":
        path, old, new = _path(root, args.get("path")), args.get("old_text"), args.get("new_text")
        if not isinstance(old, str) or not isinstance(new, str): raise ValueError("edit text must be strings")
        relative = path.relative_to(root).as_posix()
        if any(fnmatch(relative, pattern) for pattern in forbidden_paths) or not any(fnmatch(relative, pattern) for pattern in allowed_paths): raise PermissionError(f"path denied by Trial policy: {relative}")
        source = path.read_text(encoding="utf-8")
        if source.count(old) != 1: raise ValueError("old_text must occur exactly once")
        path.write_text(source.replace(old, new, 1), encoding="utf-8"); return f"edited {path.relative_to(root)}", False
    raise PermissionError(f"unsupported or denied tool: {name}")


def execute(root: Path, name: str, args: dict[str, Any], allowed_paths: list[str], forbidden_paths: list[str], test_command: str) -> str:
    """Compatibility wrapper for callers that only need model-visible text."""
    return execute_with_outcome(root, name, args, allowed_paths, forbidden_paths, test_command)[0]


def should_stop_after_tool(version: str, name: str, verification_passed: bool) -> bool:
    """V4.1's only runtime intervention: stop after successful verification."""
    return version.endswith("-v4.1") and name == "bash" and verification_passed


def is_negative_control(version: str) -> bool:
    """Return whether this version measures post-terminal redundant calls."""
    return version.endswith("-v3-negative")


def should_force_negative_terminal(version: str, calls: int, max_calls: int) -> bool:
    """Reserve the tail of a tool budget for the negative-control branch."""
    return (
        is_negative_control(version)
        and calls > 0
        and calls >= max(1, max_calls - NEGATIVE_CONTROL_TOOL_CALL_RESERVE)
    )


def _same_test_command(value: str, expected: str) -> bool:
    """Accept equivalent local Python launcher names, nothing else."""
    try:
        actual_argv, expected_argv = shlex.split(value), shlex.split(expected)
    except ValueError:
        return False
    if not actual_argv or not expected_argv:
        return False
    if actual_argv[0] in {"python", "python3", "python3.11"}:
        actual_argv[0] = "python"
    if expected_argv[0] in {"python", "python3", "python3.11"}:
        expected_argv[0] = "python"
    return actual_argv == expected_argv


def main() -> None:
    if sys.argv[1:] == ["--describe-protocol"]:
        print(json.dumps(describe_protocol(json.load(sys.stdin)), ensure_ascii=False))
        return
    observer, root = AgentObserver.from_environment(), Path(os.environ["REGRESSION_WORKTREE"]).resolve()
    allowed = [name for name in json.loads(os.environ.get("REGRESSION_ALLOWED_TOOLS", "[]")) if name in SCHEMAS]
    allowed_paths = json.loads(os.environ.get("REGRESSION_ALLOWED_PATHS", "[\"**\"]"))
    forbidden_paths = json.loads(os.environ.get("REGRESSION_FORBIDDEN_PATHS", "[]"))
    version, test_command = os.environ["REGRESSION_AGENT_VERSION"], os.environ["REGRESSION_TEST_COMMAND"]
    profile_id, system = profile(version, test_command)
    client, messages = OpenAICompatibleClient.from_environment(), [{"role": "system", "content": system}, {"role": "user", "content": os.environ["REGRESSION_PROMPT"]}]
    max_calls, max_tokens, calls = int(os.environ.get("REGRESSION_MAX_TOOL_CALLS", "20")), int(os.environ.get("REGRESSION_MAX_TOKENS", "1000")), 0
    redundant_completions_remaining = 0
    with observer.run(agent_profile=profile_id):
        while True:
            if redundant_completions_remaining == 0 and should_force_negative_terminal(version, calls, max_calls):
                redundant_completions_remaining = NEGATIVE_CONTROL_REDUNDANT_COMPLETIONS
                observer.event(
                    "negative_control_terminal_forced",
                    reason="tool_budget_reserved",
                    tool_calls=calls,
                    max_tool_calls=max_calls,
                    reserve=NEGATIVE_CONTROL_TOOL_CALL_RESERVE,
                )
            redundant_completion = redundant_completions_remaining > 0
            if redundant_completion:
                ordinal = NEGATIVE_CONTROL_REDUNDANT_COMPLETIONS - redundant_completions_remaining + 1
                observer.event(
                    "negative_control_redundant_call",
                    reason="v3_normal_completion",
                    ordinal=ordinal,
                    total=NEGATIVE_CONTROL_REDUNDANT_COMPLETIONS,
                )
            with observer.model_call(model=client.model, message_count=len(messages), tool_count=len(allowed), max_tokens=max_tokens) as span:
                tools = [{"type": "function", "function": {"name": name, "description": (f"Run only this exact verification command: {test_command}" if name == "bash" else f"worktree tool {name}"), "parameters": SCHEMAS[name], "strict": True}} for name in allowed]
                try:
                    reply = client.complete(messages, tools, max_tokens)
                except ModelClientError as exc:
                    span.end("error", model_failure_kind=exc.kind)
                    observer.event("error", error_type="model_failed", model_failure_kind=exc.kind)
                    raise
                span.record_usage(reply.usage); span.end(finish_reason=reply.finish_reason, tool_call_count=len(reply.tool_calls))
            if redundant_completion:
                redundant_completions_remaining -= 1
                if redundant_completions_remaining:
                    continue
                observer.event("agent.stop", reason="negative_control_redundant_calls_completed")
                AgentObserver.write_agent_output(
                    "Negative control completed two redundant model calls after the V3 terminal reply.",
                    "negative_control_redundant_calls",
                )
                return
            if not reply.tool_calls:
                if is_negative_control(version):
                    redundant_completions_remaining = NEGATIVE_CONTROL_REDUNDANT_COMPLETIONS
                    continue
                observer.event("agent.stop", reason="model_completed")
                AgentObserver.write_agent_output(reply.text, "model_completed")
                return
            messages.append({"role": "assistant", "content": reply.text or None, "tool_calls": [{"id": call.call_id, "type": "function", "function": {"name": call.name, "arguments": json.dumps(call.arguments)}} for call in reply.tool_calls]})
            for call in reply.tool_calls:
                calls += 1
                semantic_attrs = semantic_tool_attributes(call.name, call.arguments, worktree=root)
                with observer.tool_call(call.name, tool_use_id=call.call_id, **semantic_attrs) as span:
                    try:
                        output, verification_passed = execute_with_outcome(root, call.name, call.arguments, allowed_paths, forbidden_paths, test_command); span.preview(output)
                    except Exception as exc:
                        output, verification_passed = f"Error: {type(exc).__name__}: {exc}", False; span.end("denied" if isinstance(exc, PermissionError) else "error", error_type=type(exc).__name__)
                messages.append({"role": "tool", "tool_call_id": call.call_id, "content": output})
                if should_stop_after_tool(version, call.name, verification_passed):
                    observer.event("agent.stop", reason="verification_passed_policy")
                    AgentObserver.write_agent_output("Verification command passed; execution stopped by the V4.1 success policy.", "verification_passed")
                    return
            if should_force_negative_terminal(version, calls, max_calls):
                redundant_completions_remaining = NEGATIVE_CONTROL_REDUNDANT_COMPLETIONS
                observer.event(
                    "negative_control_terminal_forced",
                    reason="tool_budget_reserved",
                    tool_calls=calls,
                    max_tool_calls=max_calls,
                    reserve=NEGATIVE_CONTROL_TOOL_CALL_RESERVE,
                )
                continue
            if calls >= max_calls:
                break
    observer.event("agent.stop", reason="max_tool_calls")
    AgentObserver.write_agent_output("Tool-call budget exhausted.", "max_tool_calls")


if __name__ == "__main__":
    try:
        main()
    except ModelClientError as exc:
        # A controlled output lets the Adapter distinguish a provider failure
        # from malformed Agent code without recording provider secrets, prompts,
        # or provider response bodies.
        AgentObserver.write_agent_output("Model request failed.", "model_error", model_failure_kind=exc.kind)
