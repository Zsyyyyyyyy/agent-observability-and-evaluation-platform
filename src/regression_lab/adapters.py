"""Registry and stable process contract for Agent adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class AdapterError(ValueError):
    """Raised when a requested adapter is not registered or cannot run."""


@dataclass(frozen=True)
class AdapterDescriptor:
    """A Worker-process implementation of the Regression Lab adapter contract."""

    adapter_id: str
    worker_path: Path
    default_version: str
    description: str
    capabilities: tuple[str, ...]

    def as_spec(self) -> dict[str, object]:
        return {
            "id": self.adapter_id,
            "default_version": self.default_version,
            "capabilities": list(self.capabilities),
        }


REGRESSION_ROOT = Path(__file__).resolve().parents[2]
_REGISTRY: Mapping[str, AdapterDescriptor] = {
    "s20-replay": AdapterDescriptor(
        adapter_id="s20-replay",
        worker_path=REGRESSION_ROOT / "adapters" / "s20" / "worker.py",
        default_version="s20-baseline-replay-v1",
        description="Deterministic s20 replay adapter used to verify the evaluation pipeline.",
        capabilities=("read_file", "write_file", "edit_file", "glob", "bash"),
    ),
    "react-agent": AdapterDescriptor(
        adapter_id="react-agent",
        worker_path=REGRESSION_ROOT / "adapters" / "react_agent" / "worker.py",
        default_version="react-agent-v1",
        description="Minimal OpenAI-compatible real-model ReAct coding agent.",
        capabilities=("read_file", "write_file", "edit_file", "glob", "bash"),
    ),
    "failure-probe": AdapterDescriptor(
        adapter_id="failure-probe",
        worker_path=REGRESSION_ROOT / "adapters" / "failure_probe" / "worker.py",
        default_version="failure-probe-v1",
        description="Deterministic fault-injection adapter used only to verify failure handling.",
        capabilities=("read_file", "write_file", "edit_file", "glob", "bash"),
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
