"""AgentSpec v1 静态解析：用于 external-command 接入流程。"""

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
from regression_lab.protocol import agent_source_snapshot


AGENT_SPEC_SCHEMA_VERSION = 1
OBSERVATION_MODES = frozenset({"blackbox", "sdk", "langgraph"})
_TEMPLATE_FIELDS = frozenset({"workspace", "task", "agent_source"})
_PROTECTED_FIELDS = frozenset({
    "trial_id", "trace_id", "trace_path", "trace_output", "result_path", "result_output",
    "attempt_id", "attempt_path", "run_store", "output_dir", "worktree",
})
_SDK_CAPABILITY_FIELDS = frozenset({
    "model_usage", "tool_trace", "tool_semantics", "test_trace", "context_trace",
    "workflow_trace", "mcp_trace",
})


class AgentSpecError(ValueError):
    """AgentSpec 无法安全转换为 Trial 输入时抛出。"""


@dataclass(frozen=True)
class AgentSpec:
    """用户侧 Agent 的身份标识、启动命令及可选的观测能力声明。"""

    path: Path
    project_id: str | None
    agent_id: str
    version: str
    command: tuple[str, ...]
    source_root: Path | None
    observation_mode: str
    capabilities: AdapterCapabilities

    def resolve_command(self, *, workspace: str, task: str) -> list[str]:
        """将 command 中的 {workspace} / {task} 模板替换为实际值。

        AgentSpec v1 只允许这两个由 Trial 拥有的运行时字段出现在模板中。
        """

        values = {"workspace": workspace, "task": task, "agent_source": str(self.source_root or "{agent_source}")}
        return [argument.format(**values) for argument in self.command]

    def as_external_command_config(self, *, workspace: str | None = None, task: str | None = None) -> dict[str, object]:
        """返回 external-command Adapter 所需的标准化配置。

        若未传入 workspace/task，则保留原始模板命令，由 Worker 在拿到
        Trial Worktree 和任务提示词后再做替换。
        """

        if (workspace is None) != (task is None):
            raise AgentSpecError("workspace and task must be provided together when resolving runtime.command")
        command = list(self.command) if workspace is None else self.resolve_command(workspace=workspace, task=task or "")
        return {
            "adapter": "external-command",
            "agent_version": self.version,
            "external_command": command,
            **({"agent_source_root": str(self.source_root)} if self.source_root is not None else {}),
            "adapter_capabilities": self.capabilities.as_dict(),
            "observation_mode": self.observation_mode,
        }

    def snapshot(self) -> dict[str, object]:
        """返回 Experiment 使用的、可移植的内容寻址身份快照。

        只对可直接定位的入口文件做哈希：argv 无法可靠标识整个外部仓库，
        因此源码范围必须在 Artifact 中显式声明，不能隐式推断。
        """

        source = agent_source_snapshot(self.command, self.source_root)
        normalized = {
            "schema_version": AGENT_SPEC_SCHEMA_VERSION,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "version": self.version,
            "command": list(self.command),
            "observation_mode": self.observation_mode,
            "capabilities": self.capabilities.as_dict(),
            "source_root": "{agent_source}" if self.source_root is not None else None,
        }
        canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "version": self.version,
            "observation_mode": self.observation_mode,
            "normalized_command": list(self.command),
            "capabilities": self.capabilities.as_dict(),
            "agent_spec_hash": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            **source,
        }


def _unknown_fields(value: dict[str, Any], *, allowed: set[str], path: str) -> list[str]:
    """校验字典中是否存在未授权字段，区分平台保留字段和完全不支持的字段。"""
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
    """校验单个命令参数中的模板占位符：括号必须配对，且字段名必须在白名单内。"""
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
    """根据观测模式归一化能力声明。

    blackbox 与 langgraph 的能力由平台模式固定，避免用户把未观测能力声明成可用；
    sdk 模式下用户可逐项声明，默认全部关闭，需显式开启。
    """
    raw = observation.get("capabilities")
    if mode == "blackbox":
        if raw is not None:
            errors.append("observation.capabilities is only valid when observation.mode is sdk")
        return AdapterCapabilities(
            trace=True, hierarchical_trace=False, model_usage=False, tool_trace=False,
            tool_semantics=False, test_trace=False, context_trace=False,
            workflow_trace=False, mcp_trace=False,
        )
    if mode == "langgraph":
        if raw is not None:
            errors.append("observation.capabilities is platform-defined when observation.mode is langgraph")
        return AdapterCapabilities(
            trace=True, hierarchical_trace=True, model_usage=True, tool_trace=True,
            tool_semantics=False, test_trace=False, context_trace=False,
            workflow_trace=True, mcp_trace=False,
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
    """校验并归一化启动命令：必须是非空 argv 列表，可执行文件需在 PATH 中或为绝对路径。"""
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
    """解析并校验 AgentSpec v1 文件，不启动用户 Agent。"""

    spec_path = Path(path).resolve()
    try:
        raw = load_mapping_document(spec_path, document_name="agent spec")
    except (OSError, ManifestError) as exc:
        raise AgentSpecError(str(exc)) from exc
    errors = _unknown_fields(raw, allowed={"schema_version", "project_id", "agent", "runtime", "observation"}, path="agent")
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
    errors.extend(_unknown_fields(runtime, allowed={"command", "source_root"}, path="runtime"))
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
    project_value = raw.get("project_id")
    if project_value is None:
        project_id = None
    else:
        try:
            project_id = validate_identifier(project_value, "project_id")
        except ManifestError as exc:
            errors.append(str(exc))
            project_id = None
    command, command_errors = _command_errors(runtime.get("command"), spec_path)
    errors.extend(command_errors)
    source_root_value = runtime.get("source_root")
    source_root = None
    if source_root_value is not None:
        if not isinstance(source_root_value, str) or not source_root_value:
            errors.append("runtime.source_root must be an absolute directory path")
        elif not Path(source_root_value).expanduser().is_absolute():
            errors.append("runtime.source_root must be an existing absolute directory path")
        else:
            source_root = Path(source_root_value).expanduser().resolve()
            if not source_root.is_dir():
                errors.append("runtime.source_root must be an existing absolute directory path")
    if command is not None and any("{agent_source}" in value for value in command) and source_root is None:
        errors.append("runtime.command uses {agent_source} but runtime.source_root is missing")
    mode = observation.get("mode")
    if mode not in OBSERVATION_MODES:
        errors.append("observation.mode must be blackbox, sdk, or langgraph")
        mode = ""
    capabilities = _normalize_capabilities(mode, observation, errors) if mode else None
    if errors:
        raise AgentSpecError("\n".join(f"- {error}" for error in errors))
    # 走到这里说明所有校验通过，command 和 capabilities 必然非 None
    assert command is not None and capabilities is not None
    return AgentSpec(
        path=spec_path, project_id=project_id, agent_id=agent_id, version=version, command=command, source_root=source_root,
        observation_mode=mode, capabilities=capabilities,
    )
