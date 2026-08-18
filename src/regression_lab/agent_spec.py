"""Static AgentSpec v1 parsing for the external-command onboarding flow."""

from __future__ import annotations

import os
import shutil
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from regression_lab.adapters import AdapterCapabilities
from regression_lab.manifest import ManifestError, load_mapping_document, validate_identifier


AGENT_SPEC_SCHEMA_VERSION = 1
OBSERVATION_MODES = frozenset({"blackbox", "sdk"})
_TEMPLATE_FIELDS = frozenset({"workspace", "task"})
_PROTECTED_FIELDS = frozenset({
    "trial_id", "trace_id", "trace_path", "trace_output", "result_path", "result_output",
    "attempt_id", "attempt_path", "run_store", "output_dir", "worktree",
})
_SDK_CAPABILITY_FIELDS = frozenset({
    "model_usage", "tool_trace", "tool_semantics", "test_trace", "context_trace",
    "workflow_trace", "mcp_trace",
})


class AgentSpecError(ValueError):
    """Raised when an AgentSpec cannot be safely translated to a Trial input."""


@dataclass(frozen=True)
class AgentSpec:
    """User-owned identity, launch command, and optional observability claim."""

    path: Path
    agent_id: str
    version: str
    command: tuple[str, ...]
    observation_mode: str
    capabilities: AdapterCapabilities

    def resolve_command(self, *, workspace: str, task: str) -> list[str]:
        """Resolve the only two Trial-owned values accepted by AgentSpec v1."""

        values = {"workspace": workspace, "task": task}
        return [argument.format(**values) for argument in self.command]

    def as_external_command_config(self, *, workspace: str | None = None, task: str | None = None) -> dict[str, object]:
        """Return the normalized values needed by the existing external-command Adapter.

        Commands containing templates are resolved by the external-command
        Worker after it owns the Trial Worktree and task prompt.
        """

        if (workspace is None) != (task is None):
            raise AgentSpecError("workspace and task must be provided together when resolving runtime.command")
        command = list(self.command) if workspace is None else self.resolve_command(workspace=workspace, task=task or "")
        return {
            "adapter": "external-command",
            "agent_version": self.version,
            "external_command": command,
            "adapter_capabilities": self.capabilities.as_dict(),
            "observation_mode": self.observation_mode,
        }

    def snapshot(self) -> dict[str, object]:
        """Return the portable, content-addressed identity used by an Experiment.

        This deliberately hashes only the entry point when one is directly
        addressable.  An argv does not reliably identify the whole external
        repository, so the scope must stay explicit in the Artifact.
        """

        normalized = {
            "schema_version": AGENT_SPEC_SCHEMA_VERSION,
            "agent_id": self.agent_id,
            "version": self.version,
            "command": list(self.command),
            "observation_mode": self.observation_mode,
            "capabilities": self.capabilities.as_dict(),
        }
        entrypoint = next((Path(value) for value in reversed(self.command) if Path(value).is_file()), None)
        if entrypoint is not None:
            entrypoint_hash = "sha256:" + hashlib.sha256(entrypoint.read_bytes()).hexdigest()
            source_scope = "entrypoint_only"
        else:
            entrypoint_hash = None
            source_scope = "unavailable"
        canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            "agent_id": self.agent_id,
            "version": self.version,
            "observation_mode": self.observation_mode,
            "normalized_command": list(self.command),
            "capabilities": self.capabilities.as_dict(),
            "agent_spec_hash": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "entrypoint_hash": entrypoint_hash,
            "source_scope": source_scope,
        }


def _unknown_fields(value: dict[str, Any], *, allowed: set[str], path: str) -> list[str]:
    errors = []
    for key in value:
        if key in allowed:
            continue
        if key in _PROTECTED_FIELDS:
            errors.append(f"{path}.{key} is platform-owned and must not be configured")
        else:
            errors.append(f"{path}.{key} is not supported by AgentSpec v1")
    return errors


def _template_errors(argument: str, index: int) -> list[str]:
    errors = []
    cursor = 0
    while cursor < len(argument):
        opening = argument.find("{", cursor)
        closing = argument.find("}", cursor)
        if opening < 0 and closing < 0:
            break
        if opening < 0 or (0 <= closing < opening):
            return [f"runtime.command[{index}] has an unmatched '}}'"]
        end = argument.find("}", opening + 1)
        if end < 0:
            return [f"runtime.command[{index}] has an unmatched '{{'"]
        field = argument[opening + 1:end]
        if field not in _TEMPLATE_FIELDS:
            supported = ", ".join(sorted(_TEMPLATE_FIELDS))
            errors.append(f"runtime.command[{index}] uses unsupported template {{{field}}}; supported: {supported}")
        cursor = end + 1
    return errors


def _normalize_capabilities(mode: str, observation: dict[str, Any], errors: list[str]) -> AdapterCapabilities | None:
    raw = observation.get("capabilities")
    if mode == "blackbox":
        if raw is not None:
            errors.append("observation.capabilities is only valid when observation.mode is sdk")
        # Stage 2 will let the Worker provide this platform lifecycle Trace.
        return AdapterCapabilities(
            trace=True, hierarchical_trace=False, model_usage=False, tool_trace=False,
            tool_semantics=False, test_trace=False, context_trace=False,
            workflow_trace=False, mcp_trace=False,
        )
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        errors.append("observation.capabilities must be a map")
        return None
    errors.extend(_unknown_fields(raw, allowed=set(_SDK_CAPABILITY_FIELDS), path="observation.capabilities"))
    values: dict[str, object] = {
        "schema_version": 2,
        "trace": True,
        "hierarchical_trace": True,
        **{field: False for field in _SDK_CAPABILITY_FIELDS},
    }
    for field, value in raw.items():
        if field in _SDK_CAPABILITY_FIELDS:
            if not isinstance(value, bool):
                errors.append(f"observation.capabilities.{field} must be a boolean")
            else:
                values[field] = value
    if values["tool_semantics"] is True and values["tool_trace"] is not True:
        errors.append("observation.capabilities.tool_semantics requires tool_trace: true")
    capabilities = AdapterCapabilities.from_snapshot(values)
    if capabilities is None:
        errors.append("observation.capabilities could not be normalized by Adapter Capability Contract v2")
    return capabilities


def _command_errors(command: Any, spec_path: Path) -> tuple[tuple[str, ...] | None, list[str]]:
    if not isinstance(command, list) or not command:
        return None, ["runtime.command must be a non-empty argv list, not a shell command string"]
    errors = []
    normalized = []
    for index, value in enumerate(command):
        if not isinstance(value, str) or not value:
            errors.append(f"runtime.command[{index}] must be a non-empty string")
            continue
        normalized.append(value)
        errors.extend(_template_errors(value, index))
    if errors:
        return None, errors
    executable = normalized[0]
    if "{" in executable:
        errors.append("runtime.command[0] must be a concrete executable, not a template")
    elif os.path.sep in executable:
        executable_path = Path(executable)
        if not executable_path.is_absolute():
            errors.append("runtime.command[0] must be absolute when it contains a path separator")
        elif not executable_path.is_file() or not os.access(executable_path, os.X_OK):
            errors.append(f"runtime.command[0] is not an executable file: {executable}")
    elif shutil.which(executable) is None:
        errors.append(f"runtime.command[0] was not found on PATH: {executable}")
    for index, value in enumerate(normalized[1:], start=1):
        if "{" in value or not Path(value).is_absolute():
            continue
        if not Path(value).exists():
            errors.append(f"runtime.command[{index}] absolute path does not exist: {value}")
    return tuple(normalized), errors


def load_agent_spec(path: str | Path) -> AgentSpec:
    """Parse and validate AgentSpec v1 without starting the user Agent."""

    spec_path = Path(path).resolve()
    try:
        raw = load_mapping_document(spec_path, document_name="agent spec")
    except (OSError, ManifestError) as exc:
        raise AgentSpecError(str(exc)) from exc
    errors = _unknown_fields(raw, allowed={"schema_version", "agent", "runtime", "observation"}, path="agent")
    if raw.get("schema_version") != AGENT_SPEC_SCHEMA_VERSION:
        errors.append(f"schema_version must be {AGENT_SPEC_SCHEMA_VERSION}")
    agent = raw.get("agent")
    runtime = raw.get("runtime")
    observation = raw.get("observation")
    if not isinstance(agent, dict):
        errors.append("agent must be a map")
        agent = {}
    if not isinstance(runtime, dict):
        errors.append("runtime must be a map")
        runtime = {}
    if not isinstance(observation, dict):
        errors.append("observation must be a map")
        observation = {}
    errors.extend(_unknown_fields(agent, allowed={"id", "version"}, path="agent"))
    errors.extend(_unknown_fields(runtime, allowed={"command"}, path="runtime"))
    errors.extend(_unknown_fields(observation, allowed={"mode", "capabilities"}, path="observation"))
    try:
        agent_id = validate_identifier(agent.get("id"), "agent.id")
    except ManifestError as exc:
        errors.append(str(exc))
        agent_id = ""
    try:
        version = validate_identifier(agent.get("version"), "agent.version")
    except ManifestError as exc:
        errors.append(str(exc))
        version = ""
    command, command_errors = _command_errors(runtime.get("command"), spec_path)
    errors.extend(command_errors)
    mode = observation.get("mode")
    if mode not in OBSERVATION_MODES:
        errors.append("observation.mode must be blackbox or sdk")
        mode = ""
    capabilities = _normalize_capabilities(mode, observation, errors) if mode else None
    if errors:
        raise AgentSpecError("\n".join(f"- {error}" for error in errors))
    assert command is not None and capabilities is not None
    return AgentSpec(
        path=spec_path, agent_id=agent_id, version=version, command=command,
        observation_mode=mode, capabilities=capabilities,
    )
