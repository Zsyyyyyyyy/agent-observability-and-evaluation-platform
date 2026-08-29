"""LangGraph Callback 到 Regression Lab Trace 的最小映射。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from regression_lab_observer.trace import JsonlTraceWriter

try:  # 让基础 SDK 安装不依赖 LangGraph；只有用户选择该模式时才需要框架。
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:  # pragma: no cover - 由无 LangGraph 的 Observer 单元测试覆盖
    class BaseCallbackHandler:  # type: ignore[no-redef]
        pass


def _value(mapping: object, *keys: str) -> str | None:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _model_name(serialized: object, metadata: object) -> str:
    serialized_dict = serialized if isinstance(serialized, dict) else {}
    kwargs = serialized_dict.get("kwargs") if isinstance(serialized_dict.get("kwargs"), dict) else {}
    return _value(kwargs, "model_name", "model") or _value(metadata, "ls_model_name", "model_name") or "unknown"


def _usage(response: object) -> dict[str, int]:
    """只规范化 Token 计数，绝不把模型响应或消息写入 Trace。"""

    candidates: list[object] = []
    if isinstance(response, dict):
        candidates.extend([response.get("llm_output"), response.get("usage_metadata")])
    else:
        candidates.extend([getattr(response, "llm_output", None), getattr(response, "usage_metadata", None)])
    generations = response.get("generations") if isinstance(response, dict) else getattr(response, "generations", None)
    if isinstance(generations, list) and generations and isinstance(generations[0], list) and generations[0]:
        message = getattr(generations[0][0], "message", None)
        candidates.append(getattr(message, "usage_metadata", None))
    for candidate in list(candidates):
        if isinstance(candidate, dict) and isinstance(candidate.get("token_usage"), dict):
            candidates.append(candidate["token_usage"])
    aliases = {
        "prompt_tokens": ("prompt_tokens", "input_tokens"),
        "completion_tokens": ("completion_tokens", "output_tokens"),
        "total_tokens": ("total_tokens",),
    }
    normalized: dict[str, int] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for target, keys in aliases.items():
            if target in normalized:
                continue
            for key in keys:
                value = candidate.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    normalized[target] = value
                    break
    if "total_tokens" not in normalized and {"prompt_tokens", "completion_tokens"} <= normalized.keys():
        normalized["total_tokens"] = normalized["prompt_tokens"] + normalized["completion_tokens"]
    return normalized


class _LangGraphCallback(BaseCallbackHandler):
    """只保存 Callback Run ID 与平台 Span 的映射，不持久化原始输入输出。"""

    raise_error = False

    def __init__(self, observer: "LangGraphObserver"):
        self.observer = observer
        self._span_by_run: dict[str, str] = {}
        self._parent_by_run: dict[str, str] = {}

    @staticmethod
    def _run_id(run_id: object) -> str:
        return str(run_id)

    def _parent_span(self, parent_run_id: object) -> str:
        parent = self._run_id(parent_run_id) if parent_run_id is not None else ""
        return self._span_by_run.get(parent) or self._parent_by_run.get(parent) or self.observer.root_span_id

    def _start(self, run_id: object, parent_run_id: object, name: str, span_type: str, **attrs: Any) -> None:
        try:
            run = self._run_id(run_id)
            self._span_by_run[run] = self.observer.writer.start_span(name, self._parent_span(parent_run_id), span_type=span_type, **attrs)
        except Exception as exc:  # Callback 不能改变 Agent 主流程，但必须留下不可晋级标记。
            self.observer.record_callback_error(exc)

    def _end(self, run_id: object, status: str = "ok", **attrs: Any) -> None:
        run = self._run_id(run_id)
        span_id = self._span_by_run.pop(run, None)
        if span_id is None:
            return
        try:
            self.observer.writer.end_span(span_id, status=status, duration_ms=self.observer.elapsed(run), **attrs)
        except Exception as exc:
            self.observer.record_callback_error(exc)

    def on_chain_start(self, serialized: object, inputs: object, *, run_id: object, parent_run_id: object | None = None, metadata: object = None, **_: Any) -> None:
        run = self._run_id(run_id)
        self.observer.started(run)
        parent_span = self._parent_span(parent_run_id)
        self._parent_by_run[run] = parent_span
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        node = _value(metadata_dict, "langgraph_node")
        if node:
            self._start(run_id, parent_run_id, f"workflow.{node}", "workflow", node=node)

    def on_chain_end(self, outputs: object, *, run_id: object, **_: Any) -> None:
        self._end(run_id)
        self._parent_by_run.pop(self._run_id(run_id), None)

    def on_chain_error(self, error: BaseException, *, run_id: object, **_: Any) -> None:
        self._end(run_id, "error", error_type=type(error).__name__)
        self._parent_by_run.pop(self._run_id(run_id), None)

    def _start_model(self, serialized: object, *, run_id: object, parent_run_id: object | None, metadata: object = None) -> None:
        run = self._run_id(run_id)
        self.observer.started(run)
        self._start(run_id, parent_run_id, "model.call", "llm", model=_model_name(serialized, metadata))

    def on_llm_start(self, serialized: object, prompts: object, *, run_id: object, parent_run_id: object | None = None, metadata: object = None, **_: Any) -> None:
        self._start_model(serialized, run_id=run_id, parent_run_id=parent_run_id, metadata=metadata)

    def on_chat_model_start(self, serialized: object, messages: object, *, run_id: object, parent_run_id: object | None = None, metadata: object = None, **_: Any) -> None:
        self._start_model(serialized, run_id=run_id, parent_run_id=parent_run_id, metadata=metadata)

    def on_llm_end(self, response: object, *, run_id: object, **_: Any) -> None:
        usage = _usage(response)
        self._end(run_id, usage=usage) if usage else self._end(run_id)

    def on_llm_error(self, error: BaseException, *, run_id: object, **_: Any) -> None:
        self._end(run_id, "error", error_type=type(error).__name__)

    def on_tool_start(self, serialized: object, input_str: object, *, run_id: object, parent_run_id: object | None = None, **_: Any) -> None:
        self.observer.started(self._run_id(run_id))
        tool_name = _value(serialized, "name") or "unknown"
        self._start(run_id, parent_run_id, "tool.call", "tool", tool_name=tool_name)

    def on_tool_end(self, output: object, *, run_id: object, **_: Any) -> None:
        self._end(run_id)

    def on_tool_error(self, error: BaseException, *, run_id: object, **_: Any) -> None:
        self._end(run_id, "error", error_type=type(error).__name__)

    def close_open_spans(self) -> None:
        for run_id, span_id in reversed(list(self._span_by_run.items())):
            self.observer.writer.end_span(span_id, status="error", duration_ms=self.observer.elapsed(run_id), error_type="callback_unclosed")
        self._span_by_run.clear()


class LangGraphObserver:
    """在一次 LangGraph 调用周围建立平台兼容、脱敏的 Trace。"""

    def __init__(self, trace_path: str | Path, trace_id: str, *, trial_id: str, case_id: str, agent_version: str, adapter_id: str):
        self.writer = JsonlTraceWriter(trace_path, trace_id)
        self.trial_id = trial_id
        self.case_id = case_id
        self.agent_version = agent_version
        self.adapter_id = adapter_id
        self.root_span_id = ""
        self.callback = _LangGraphCallback(self)
        self._started: dict[str, float] = {}
        self._errors: list[str] = []
        self._status_path = Path(os.environ.get("REGRESSION_OBSERVATION_STATUS_PATH", str(Path(trace_path).with_name("observation-status.json"))))

    @classmethod
    def from_environment(cls) -> "LangGraphObserver":
        required = ("REGRESSION_TRACE_PATH", "REGRESSION_TRACE_ID", "REGRESSION_TRIAL_ID", "REGRESSION_CASE_ID", "REGRESSION_AGENT_VERSION")
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise ValueError("missing Regression Lab environment: " + ", ".join(missing))
        return cls(
            os.environ["REGRESSION_TRACE_PATH"], os.environ["REGRESSION_TRACE_ID"],
            trial_id=os.environ["REGRESSION_TRIAL_ID"], case_id=os.environ["REGRESSION_CASE_ID"],
            agent_version=os.environ["REGRESSION_AGENT_VERSION"], adapter_id=os.environ.get("REGRESSION_ADAPTER_ID", "external-command"),
        )

    def started(self, run_id: str) -> None:
        self._started[run_id] = time.monotonic()

    def elapsed(self, run_id: str) -> float:
        started = self._started.pop(run_id, None)
        return round((time.monotonic() - started) * 1000, 3) if started is not None else 0.0

    def record_callback_error(self, error: BaseException) -> None:
        self._errors.append(type(error).__name__)

    def __enter__(self) -> "LangGraphObserver":
        self.started("root")
        self.root_span_id = self.writer.start_span(
            "agent.run", span_type="agent", trial_id=self.trial_id, case_id=self.case_id,
            agent_version=self.agent_version, adapter_id=self.adapter_id,
            observation_mode="langgraph", trace_origin="framework", trace_scope="langgraph_callback",
        )
        return self

    def __exit__(self, exc_type: object, exc: BaseException | None, traceback: object) -> bool:
        self.callback.close_open_spans()
        status = "error" if exc is not None or self._errors else "ok"
        attrs: dict[str, Any] = {"duration_ms": self.elapsed("root")}
        if exc is not None:
            attrs["error_type"] = type(exc).__name__
        if self._errors:
            attrs["observation_error"] = self._errors[-1]
        self.writer.end_span(self.root_span_id, status=status, **attrs)
        status = {"complete": exc is None and not self._errors, "errors": self._errors}
        self._status_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._status_path.with_suffix(self._status_path.suffix + ".tmp")
        temp.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        temp.replace(self._status_path)
        return False
