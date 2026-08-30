"""无外部依赖地校验 Regression Lab JSONL Trace 契约。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from regression_lab.trace import SPAN_TYPES


TRACE_KINDS = {"span_start", "span_end", "event"}

_SPAN_TYPE_PREFIXES = (
    ("agent.", "agent"),
    ("model.", "llm"),
    ("llm.", "llm"),
    ("tool.", "tool"),
    ("test.", "test"),
    ("retrieval.", "retrieval"),
    ("context.", "context"),
    ("workflow.", "workflow"),
    ("mcp.", "mcp"),
)


def infer_span_type(name: str) -> str:
    """为未记录类型的 v0 Span 推断兼容的 v1 类型。"""

    for prefix, span_type in _SPAN_TYPE_PREFIXES:
        if name.startswith(prefix):
            return span_type
    return "other"


def span_type_for(event: dict[str, Any]) -> str:
    """优先返回显式 v1 类型，否则按名称推断兼容的 v0 类型。"""

    span_type = event.get("span_type")
    if isinstance(span_type, str):
        return span_type
    name = event.get("name")
    return infer_span_type(name) if isinstance(name, str) else "other"


@dataclass(frozen=True)
class TraceValidation:
    valid: bool
    trace_id: str | None
    event_count: int
    span_count: int
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "trace_id": self.trace_id,
            "event_count": self.event_count,
            "span_count": self.span_count,
            "errors": list(self.errors),
        }


def validate_events(
    events: Iterable[dict[str, Any]],
    *,
    expected_trace_id: str | None = None,
    expected_trial_id: str | None = None,
    expected_root_attributes: dict[str, str] | None = None,
) -> TraceValidation:
    """校验事件顺序、身份和 Span 生命周期不变量。"""

    errors: list[str] = []
    trace_id: str | None = None
    previous_seq = 0
    started_span_ids: set[str] = set()
    ended_span_ids: set[str] = set()
    parent_by_span: dict[str, str | None] = {}
    root_span_ids: set[str] = set()
    event_count = 0

    for index, event in enumerate(events, start=1):
        event_count += 1
        if not isinstance(event, dict):
            errors.append(f"event {index}: record must be an object")
            continue

        event_trace_id = event.get("trace_id")
        if not isinstance(event_trace_id, str) or not event_trace_id:
            errors.append(f"event {index}: trace_id is required")
        elif trace_id is None:
            trace_id = event_trace_id
        elif event_trace_id != trace_id:
            errors.append(f"event {index}: trace_id changed")
        if expected_trace_id and event_trace_id != expected_trace_id:
            errors.append(f"event {index}: unexpected trace_id")

        event_seq = event.get("event_seq")
        if not isinstance(event_seq, int) or isinstance(event_seq, bool):
            errors.append(f"event {index}: event_seq must be an integer")
        elif event_seq <= previous_seq:
            errors.append(f"event {index}: event_seq is not strictly increasing")
        else:
            previous_seq = event_seq

        timestamp = event.get("ts")
        if (
            not isinstance(timestamp, (int, float))
            or isinstance(timestamp, bool)
            or not math.isfinite(timestamp)
        ):
            errors.append(f"event {index}: ts must be numeric")

        kind = event.get("kind")
        if kind not in TRACE_KINDS:
            errors.append(f"event {index}: unsupported kind {kind!r}")
            continue

        if kind == "span_start":
            span_id = event.get("span_id")
            valid_span_id = isinstance(span_id, str) and bool(span_id)
            if not valid_span_id:
                errors.append(f"event {index}: span_start requires span_id")
            elif span_id in started_span_ids:
                errors.append(f"event {index}: duplicate span_start {span_id}")
            else:
                started_span_ids.add(span_id)
                parent_by_span[span_id] = event.get("parent_span_id")
            if not isinstance(event.get("name"), str) or not event["name"]:
                errors.append(f"event {index}: span_start requires name")
            attributes = event.get("attributes")
            if not isinstance(attributes, dict):
                errors.append(f"event {index}: span_start attributes must be an object")
            if "span_type" in event and (
                not isinstance(event["span_type"], str) or event["span_type"] not in SPAN_TYPES
            ):
                errors.append(f"event {index}: unsupported span_type {event.get('span_type')!r}")
            parent_id = event.get("parent_span_id")
            if parent_id is None and valid_span_id:
                root_span_ids.add(span_id)
            elif not isinstance(parent_id, str) or parent_id not in started_span_ids:
                errors.append(f"event {index}: parent span must already exist")
            elif parent_id in ended_span_ids:
                errors.append(f"event {index}: parent span has already ended")
            if event.get("name") == "agent.run" and expected_trial_id:
                trial_id = attributes.get("trial_id") if isinstance(attributes, dict) else None
                if trial_id != expected_trial_id:
                    errors.append(f"event {index}: agent.run trial_id does not match")
            if event.get("name") == "agent.run" and expected_root_attributes:
                for key, expected in expected_root_attributes.items():
                    if not isinstance(attributes, dict) or attributes.get(key) != expected:
                        errors.append(f"event {index}: agent.run {key} does not match")

        elif kind == "span_end":
            span_id = event.get("span_id")
            if not isinstance(span_id, str) or not span_id:
                errors.append(f"event {index}: span_end requires span_id")
            elif span_id not in started_span_ids:
                errors.append(f"event {index}: span_end without span_start {span_id}")
            elif span_id in ended_span_ids:
                errors.append(f"event {index}: duplicate span_end {span_id}")
            else:
                open_children = sorted(
                    child_id for child_id, parent_id in parent_by_span.items()
                    if parent_id == span_id and child_id not in ended_span_ids
                )
                if open_children:
                    errors.append(
                        f"event {index}: span_end before child span ended: {', '.join(open_children)}"
                    )
                ended_span_ids.add(span_id)
            if not isinstance(event.get("status"), str) or not event["status"]:
                errors.append(f"event {index}: span_end requires status")
            if not isinstance(event.get("attributes"), dict):
                errors.append(f"event {index}: span_end attributes must be an object")

        else:
            if not isinstance(event.get("name"), str) or not event["name"]:
                errors.append(f"event {index}: event requires name")
            if "attributes" in event and not isinstance(event.get("attributes"), dict):
                errors.append(f"event {index}: event attributes must be an object")
            parent_id = event.get("parent_span_id")
            if parent_id is not None and (
                not isinstance(parent_id, str) or parent_id not in started_span_ids
            ):
                errors.append(f"event {index}: event parent span must already exist")
            elif isinstance(parent_id, str) and parent_id in ended_span_ids:
                errors.append(f"event {index}: event parent span has already ended")

    if not event_count:
        errors.append("trace must contain at least one event")
    if len(root_span_ids) > 1:
        errors.append("trace must not contain multiple root spans")
    if expected_trial_id and len(root_span_ids) != 1:
        errors.append("trial trace must contain exactly one root span")
    for span_id in sorted(started_span_ids - ended_span_ids):
        errors.append(f"span missing end: {span_id}")

    return TraceValidation(
        valid=not errors,
        trace_id=trace_id,
        event_count=event_count,
        span_count=len(started_span_ids),
        errors=tuple(errors),
    )


def validate_trace(
    path: str | Path,
    *,
    expected_trace_id: str | None = None,
    expected_trial_id: str | None = None,
    expected_root_attributes: dict[str, str] | None = None,
) -> TraceValidation:
    """读取并校验 JSONL Trace，把格式错误的行纳入校验结果。"""

    errors: list[str] = []
    events: list[dict[str, Any]] = []
    trace_path = Path(path)
    if not trace_path.exists():
        return TraceValidation(False, None, 0, 0, (f"trace file missing: {trace_path}",))
    for line_number, line in enumerate(trace_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: invalid JSON ({exc.msg})")
            continue
        if not isinstance(decoded, dict):
            errors.append(f"line {line_number}: record must be an object")
            continue
        events.append(decoded)
    validation = validate_events(
        events,
        expected_trace_id=expected_trace_id,
        expected_trial_id=expected_trial_id,
        expected_root_attributes=expected_root_attributes,
    )
    return TraceValidation(
        valid=validation.valid and not errors,
        trace_id=validation.trace_id,
        event_count=validation.event_count,
        span_count=validation.span_count,
        errors=tuple(errors) + validation.errors,
    )


def trace_conformance(events: Iterable[dict[str, Any]], observation_mode: str) -> dict[str, Any]:
    """校验观测模式承诺的最低 Trace 语义，不把缺失能力伪装成零。"""

    records = list(events)
    starts = [event for event in records if event.get("kind") == "span_start"]
    names = {str(event.get("name")) for event in starts}
    required = {
        "blackbox": {"agent.run"},
        "sdk": {"agent.run"},
        "langgraph": {"agent.run", "workflow"},
    }
    errors: list[str] = []
    if observation_mode not in required:
        errors.append(f"unsupported observation mode: {observation_mode}")
    elif "agent.run" not in names:
        errors.append("missing agent.run root span")
    elif observation_mode == "langgraph" and not any(name.startswith("workflow.") for name in names):
        errors.append("langgraph trace requires at least one workflow span")
    return {
        "profile": f"{observation_mode}_v1",
        "valid": not errors,
        "errors": errors,
        "observed": {
            "workflow": any(name.startswith("workflow.") for name in names),
            "model": any(name in {"model.call", "llm.call"} for name in names),
            "tool": any(name == "tool.call" for name in names),
        },
    }
