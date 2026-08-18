"""Registry and stable process contract for Agent adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class AdapterError(ValueError):
    """Raised when a requested adapter is not registered or cannot run."""


@dataclass(frozen=True)
class AdapterCapabilities:
    """Evidence an Adapter can emit, independent of the tools it may invoke."""

    trace: bool
    hierarchical_trace: bool
    model_usage: bool
    tool_trace: bool
    tool_semantics: bool
    test_trace: bool
    context_trace: bool
    workflow_trace: bool
    mcp_trace: bool

    def as_dict(self) -> dict[str, object]:
        return {"schema_version": 2, **{
            field: getattr(self, field)
            for field in (
                "trace", "hierarchical_trace", "model_usage", "tool_trace", "tool_semantics",
                "test_trace", "context_trace", "workflow_trace", "mcp_trace",
            )
        }}

    @classmethod
    def from_snapshot(cls, value: object) -> "AdapterCapabilities | None":
        if not isinstance(value, dict):
            return None
        fields = (
            "trace", "hierarchical_trace", "model_usage", "tool_trace", "tool_semantics",
            "test_trace", "context_trace", "workflow_trace", "mcp_trace",
        )
        if not all(isinstance(value.get(field), bool) for field in fields):
            return None
        return cls(**{field: value[field] for field in fields})


@dataclass(frozen=True)
class AdapterDescriptor:
    """A Worker-process implementation of the Regression Lab adapter contract."""

    adapter_id: str
    worker_path: Path
    default_version: str
    description: str
    capabilities: tuple[str, ...]
    evidence_capabilities: AdapterCapabilities

    def as_spec(self) -> dict[str, object]:
        return {
            "id": self.adapter_id,
            "default_version": self.default_version,
            "capabilities": list(self.capabilities),
            "evidence_capabilities": self.evidence_capabilities.as_dict(),
        }


REGRESSION_ROOT = Path(__file__).resolve().parents[2]
_REGISTRY: Mapping[str, AdapterDescriptor] = {
    "readonly-replay": AdapterDescriptor(
        adapter_id="readonly-replay",
        worker_path=REGRESSION_ROOT / "adapters" / "readonly_replay" / "worker.py",
        default_version="readonly-replay-v1",
        description="Deterministic read-only replay adapter used to verify the evaluation pipeline.",
        capabilities=("read_file", "write_file", "edit_file", "glob", "bash"),
        evidence_capabilities=AdapterCapabilities(
            trace=True, hierarchical_trace=True, model_usage=False, tool_trace=True, tool_semantics=False,
            test_trace=False, context_trace=True, workflow_trace=False, mcp_trace=False,
        ),
    ),
    "react-agent": AdapterDescriptor(
        adapter_id="react-agent",
        worker_path=REGRESSION_ROOT / "adapters" / "react_agent" / "worker.py",
        default_version="react-agent-v1",
        description="Minimal OpenAI-compatible real-model ReAct coding agent.",
        capabilities=("read_file", "write_file", "edit_file", "glob", "bash"),
        evidence_capabilities=AdapterCapabilities(
            trace=True, hierarchical_trace=True, model_usage=True, tool_trace=True, tool_semantics=True,
            test_trace=False, context_trace=False, workflow_trace=False, mcp_trace=False,
        ),
    ),
    "failure-probe": AdapterDescriptor(
        adapter_id="failure-probe",
        worker_path=REGRESSION_ROOT / "adapters" / "failure_probe" / "worker.py",
        default_version="failure-probe-v1",
        description="Deterministic fault-injection adapter used only to verify failure handling.",
        capabilities=("read_file", "write_file", "edit_file", "glob", "bash"),
        evidence_capabilities=AdapterCapabilities(
            trace=True, hierarchical_trace=True, model_usage=False, tool_trace=True, tool_semantics=False,
            test_trace=False, context_trace=False, workflow_trace=False, mcp_trace=False,
        ),
    ),
    "external-command": AdapterDescriptor(
        adapter_id="external-command",
        worker_path=REGRESSION_ROOT / "adapters" / "external_command" / "worker.py",
        default_version="external-agent-v1",
        description="Framework-neutral local Agent command using the JSONL observer contract.",
        capabilities=("agent.run", "model.call", "tool.call"),
        evidence_capabilities=AdapterCapabilities(
            trace=True, hierarchical_trace=True, model_usage=True, tool_trace=True, tool_semantics=True,
            test_trace=False, context_trace=False, workflow_trace=False, mcp_trace=False,
        ),
    ),
}


def get_adapter(adapter_id: str) -> AdapterDescriptor:
    """Resolve a registered adapter and ensure its Worker entry point exists."""

    adapter = _REGISTRY.get(adapter_id)
    if adapter is None:
        raise AdapterError(f"unknown adapter {adapter_id!r}; available: {', '.join(sorted(_REGISTRY))}")
    if not adapter.worker_path.is_file():
        raise AdapterError(f"adapter worker missing: {adapter.worker_path}")
    return adapter


def registered_adapters() -> tuple[AdapterDescriptor, ...]:
    return tuple(_REGISTRY[key] for key in sorted(_REGISTRY))


def capabilities_for_result(result: dict[str, Any]) -> tuple[AdapterCapabilities | None, str]:
    """Resolve persisted evidence capability, with an explicit legacy registry fallback."""

    snapshot = AdapterCapabilities.from_snapshot(result.get("adapter_capabilities"))
    if snapshot is not None:
        return snapshot, "artifact_snapshot"
    adapter_id = result.get("adapter_id")
    descriptor = _REGISTRY.get(adapter_id) if isinstance(adapter_id, str) else None
    if descriptor is not None:
        return descriptor.evidence_capabilities, "registry_fallback"
    return None, "historical_unknown"
