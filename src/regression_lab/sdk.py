"""Small, fail-open observer SDK for external Python Agents.

The SDK is intentionally only a JSONL event writer.  It neither evaluates a
Trial nor decides whether an Agent may be promoted; those remain platform
responsibilities in the external-command Adapter.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from regression_lab.trace import TraceCollector


class _Span(AbstractContextManager["_Span"]):
    def __init__(self, observer: "AgentObserver", name: str, parent_id: str | None, attrs: dict[str, Any], *, on_enter: Any = None):
        self.observer, self.name, self.parent_id, self.attrs = observer, name, parent_id, attrs
        self.on_enter = on_enter
        self.span_id: str | None = None
        self.started = 0.0
        self.status = "ok"
        self.end_attrs: dict[str, Any] = {}

    def __enter__(self) -> "_Span":
        self.started = time.monotonic()
        self.span_id = self.observer._start(self.name, self.parent_id, **self.attrs)
        if self.on_enter is not None:
            self.on_enter(self.span_id)
        return self

    def end(self, status: str = "ok", **attrs: Any) -> None:
        self.status = status
        self.end_attrs.update(attrs)

    def preview(self, value: Any) -> None:
        self.end_attrs["output_preview"] = str(value)[:240]

    def record_usage(self, usage: dict[str, Any] | None) -> None:
        if isinstance(usage, dict):
            self.end_attrs["usage"] = {
                key: value for key, value in usage.items()
                if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                and isinstance(value, int) and not isinstance(value, bool)
            }

    def __exit__(self, exc_type: object, exc: BaseException | None, traceback: object) -> bool:
        status = "error" if exc is not None and self.status == "ok" else self.status
        attrs = {"duration_ms": round((time.monotonic() - self.started) * 1000, 3), **self.end_attrs}
        if exc is not None:
            attrs["error_type"] = type(exc).__name__
        self.observer._end(self.span_id, status, **attrs)
        return False


class AgentObserver:
    """Best-effort observer initialized from the Adapter-owned environment."""

    def __init__(self, trace_path: str | Path, trace_id: str, *, trial_id: str, case_id: str,
                 agent_version: str, adapter_id: str, agent_profile: str | None = None):
        try:
            self.trace: TraceCollector | None = TraceCollector(trace_path, trace_id)
        except OSError:
            # Observation must not replace an Agent's primary result with an
            # SDK filesystem failure. The Adapter fails missing evidence closed.
            self.trace = None
        self.trial_id, self.case_id = trial_id, case_id
        self.agent_version, self.adapter_id, self.agent_profile = agent_version, adapter_id, agent_profile
        self._root_id: str | None = None

    @classmethod
    def from_environment(cls) -> "AgentObserver":
        required = ("REGRESSION_TRACE_PATH", "REGRESSION_TRACE_ID", "REGRESSION_TRIAL_ID", "REGRESSION_CASE_ID", "REGRESSION_AGENT_VERSION")
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise ValueError("missing Regression Lab environment: " + ", ".join(missing))
        return cls(
            os.environ["REGRESSION_TRACE_PATH"], os.environ["REGRESSION_TRACE_ID"],
            trial_id=os.environ["REGRESSION_TRIAL_ID"], case_id=os.environ["REGRESSION_CASE_ID"],
            agent_version=os.environ["REGRESSION_AGENT_VERSION"],
            adapter_id=os.environ.get("REGRESSION_ADAPTER_ID", "external-command"),
            agent_profile=os.environ.get("REGRESSION_AGENT_PROFILE") or None,
        )

    def _start(self, name: str, parent_id: str | None, **attrs: Any) -> str | None:
        if self.trace is None:
            return None
        try:
            return self.trace.start_span(name, parent_id=parent_id, **attrs)
        except OSError:
            return None

    def _end(self, span_id: str | None, status: str, **attrs: Any) -> None:
        if span_id is None or self.trace is None:
            return
        try:
            self.trace.end_span(span_id, status=status, **attrs)
        except OSError:
            pass

    def run(self, **attrs: Any) -> _Span:
        values = {"trial_id": self.trial_id, "case_id": self.case_id, "agent_version": self.agent_version,
                  "adapter_id": self.adapter_id, **({"agent_profile": self.agent_profile} if self.agent_profile else {}), **attrs}
        return _Span(self, "agent.run", None, values, on_enter=lambda span_id: setattr(self, "_root_id", span_id))

    def model_call(self, *, model: str, **attrs: Any) -> _Span:
        return _Span(self, "model.call", self._root_id, {"model": model, **attrs})

    def tool_call(self, tool_name: str, **attrs: Any) -> _Span:
        return _Span(self, "tool.call", self._root_id, {"tool_name": tool_name, **attrs})

    def event(self, name: str, **attrs: Any) -> None:
        if self.trace is None:
            return
        try:
            self.trace.event(name, parent_id=self._root_id, **attrs)
        except OSError:
            pass

    @staticmethod
    def write_agent_output(agent_response: str, agent_exit_reason: str, *, model_failure_kind: str | None = None) -> None:
        """Atomically publish the Agent-controlled result fields.

        ``model_failure_kind`` is accepted only for a model-error exit and is
        intentionally a short category, never a provider response body.
        """
        output = Path(os.environ["REGRESSION_AGENT_OUTPUT_PATH"])
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_suffix(output.suffix + ".tmp")
        payload = {"agent_response": str(agent_response)[:4096], "agent_exit_reason": str(agent_exit_reason)[:128]}
        if model_failure_kind:
            payload["model_failure_kind"] = str(model_failure_kind)[:64]
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp.replace(output)
