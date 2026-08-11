"""Worktree-scoped tools for the minimal ReAct adapter."""

from __future__ import annotations

import glob as glob_module
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from regression_lab.sandbox import DockerSandbox


SUPPORTED_TOOLS = frozenset({"read_file", "write_file", "edit_file", "glob", "bash"})
ALWAYS_DENIED_TOOLS = frozenset({"spawn_teammate", "connect_mcp", "schedule_cron", "create_worktree", "remove_worktree", "keep_worktree"})

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_file": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
    "write_file": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False},
    "edit_file": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"], "additionalProperties": False},
    "glob": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"], "additionalProperties": False},
    "bash": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"], "additionalProperties": False},
}


def resolve_tool_policy(spec: dict[str, Any]) -> tuple[frozenset[str], frozenset[str]]:
    requested, denied = spec.get("allowed_tools", sorted(SUPPORTED_TOOLS)), spec.get("denied_tools", [])
    if not isinstance(requested, list) or not all(isinstance(item, str) for item in requested):
        raise ValueError("allowed_tools must be a string list")
    if not isinstance(denied, list) or not all(isinstance(item, str) for item in denied):
        raise ValueError("denied_tools must be a string list")
    requested_set, denied_set = frozenset(requested), frozenset(denied)
    unsupported, permanent = requested_set - SUPPORTED_TOOLS, requested_set & ALWAYS_DENIED_TOOLS
    if unsupported or permanent:
        raise ValueError(f"invalid allowed_tools: unsupported={sorted(unsupported)}, permanently_denied={sorted(permanent)}")
    return requested_set - denied_set, denied_set | ALWAYS_DENIED_TOOLS


def openai_tools(allowed_tools: frozenset[str]) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": name, "description": f"Regression Lab worktree tool: {name}", "parameters": TOOL_SCHEMAS[name], "strict": True}} for name in sorted(allowed_tools)]


class ToolExecutor:
    def __init__(
        self,
        worktree: Path,
        sandbox: DockerSandbox | None,
        *,
        allowed_paths: tuple[str, ...] = ("**",),
        forbidden_paths: tuple[str, ...] = (),
    ):
        self.worktree, self.sandbox = worktree.resolve(), sandbox
        self.allowed_paths, self.forbidden_paths = allowed_paths, forbidden_paths

    def _path(self, value: Any) -> Path:
        if not isinstance(value, str) or not value:
            raise ValueError("path must be a non-empty string")
        candidate = (self.worktree / value).resolve()
        if candidate != self.worktree and self.worktree not in candidate.parents:
            raise ValueError("path escapes the trial worktree")
        return candidate

    def _mutable_path(self, value: Any) -> Path:
        """Return a worktree file only when the Trial policy permits mutation.

        The evaluators retain a second, post-run policy check. Enforcing here is
        important as well: otherwise an Agent could temporarily rewrite a test
        to obtain a passing result before the evaluator marks it invalid.
        """

        candidate = self._path(value)
        relative = candidate.relative_to(self.worktree).as_posix()
        if any(fnmatch(relative, pattern) for pattern in self.forbidden_paths):
            raise PermissionError(f"path is forbidden by trial policy: {relative}")
        if self.allowed_paths and not any(fnmatch(relative, pattern) for pattern in self.allowed_paths):
            raise PermissionError(f"path is not allowed by trial policy: {relative}")
        return candidate

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "read_file":
            return self._path(arguments.get("path")).read_text(encoding="utf-8")
        if name == "write_file":
            path, content = self._mutable_path(arguments.get("path")), arguments.get("content")
            if not isinstance(content, str): raise ValueError("content must be a string")
            path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content, encoding="utf-8"); return f"wrote {path.relative_to(self.worktree)}"
        if name == "edit_file":
            path, old, new = self._mutable_path(arguments.get("path")), arguments.get("old_text"), arguments.get("new_text")
            if not isinstance(old, str) or not isinstance(new, str): raise ValueError("old_text and new_text must be strings")
            source = path.read_text(encoding="utf-8")
            if old not in source: raise ValueError("old_text was not found exactly once")
            if source.count(old) != 1: raise ValueError("old_text must occur exactly once")
            path.write_text(source.replace(old, new, 1), encoding="utf-8"); return f"edited {path.relative_to(self.worktree)}"
        if name == "glob":
            pattern = arguments.get("pattern")
            if not isinstance(pattern, str) or Path(pattern).is_absolute() or ".." in Path(pattern).parts: raise ValueError("invalid glob pattern")
            return "\n".join(sorted(str(Path(item).relative_to(self.worktree)) for item in glob_module.glob(str(self.worktree / pattern), recursive=True)))
        if name == "bash":
            if self.sandbox is None: raise PermissionError("bash requires Docker Sandbox")
            command = arguments.get("command")
            if not isinstance(command, str) or not command: raise ValueError("command must be a non-empty string")
            result = self.sandbox.run(command)
            return (result.stdout + result.stderr).strip() or f"exit_code={result.exit_code}"
        raise ValueError(f"unsupported tool: {name}")
