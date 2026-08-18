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
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

from regression_lab.trace import SPAN_TYPES, TraceCollector


_current_span: ContextVar[tuple["AgentObserver", str] | None] = ContextVar("regression_lab_current_span", default=None)


class _Span(AbstractContextManager["_Span"]):
    def __init__(self, observer: "AgentObserver", name: str, span_type: str, attrs: dict[str, Any], *, root: bool = False):
        self.observer, self.name, self.span_type, self.attrs = observer, name, span_type, attrs
        self.root = root
        self.span_id: str | None = None
        self._context_token: Token[tuple["AgentObserver", str] | None] | None = None
        self.started = 0.0
        self.status = "ok"
        self.end_attrs: dict[str, Any] = {}

    def __enter__(self) -> "_Span":
        self.started = time.monotonic()
        parent_id = None if self.root else self.observer._current_parent_id()
        self.span_id = self.observer._start(self.name, parent_id, self.span_type, **self.attrs)
        if self.root:
            self.observer._root_id = self.span_id
        if self.span_id is not None:
            self._context_token = _current_span.set((self.observer, self.span_id))
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
        if self._context_token is not None:
            _current_span.reset(self._context_token)
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

    def _start(self, name: str, parent_id: str | None, span_type: str, **attrs: Any) -> str | None:
        if self.trace is None:
            return None
        try:
            return self.trace.start_span(name, parent_id=parent_id, span_type=span_type, **attrs)
        except OSError:
            return None

    def _end(self, span_id: str | None, status: str, **attrs: Any) -> None:
        if span_id is None or self.trace is None:
            return
        try:
            self.trace.end_span(span_id, status=status, **attrs)
        except OSError:
            pass

    def _current_parent_id(self) -> str | None:
        current = _current_span.get()
        if current is not None and current[0] is self:
            return current[1]
        return self._root_id

    def span(self, name: str, span_type: str = "other", **attrs: Any) -> _Span:
        if span_type not in SPAN_TYPES:
            raise ValueError(f"unsupported span_type: {span_type!r}")
        return _Span(self, name, span_type, attrs)

    def run(self, **attrs: Any) -> _Span:
        values = {"trial_id": self.trial_id, "case_id": self.case_id, "agent_version": self.agent_version,
                  "adapter_id": self.adapter_id, **({"agent_profile": self.agent_profile} if self.agent_profile else {}), **attrs}
        return _Span(self, "agent.run", "agent", values, root=True)

    def model_call(self, *, model: str, **attrs: Any) -> _Span:
        return self.span("model.call", "llm", model=model, **attrs)

    def tool_call(self, tool_name: str, **attrs: Any) -> _Span:
        return self.span("tool.call", "tool", tool_name=tool_name, **attrs)

    def event(self, name: str, **attrs: Any) -> None:
        if self.trace is None:
            return
        try:
            self.trace.event(name, parent_id=self._current_parent_id(), **attrs)
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
