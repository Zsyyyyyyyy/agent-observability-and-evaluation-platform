"""Dependency-free validation for the Regression Lab JSONL trace contract."""

from __future__ import annotations

import json
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
    """Infer a v1 type for v0 spans that did not record one."""

    for prefix, span_type in _SPAN_TYPE_PREFIXES:
        if name.startswith(prefix):
            return span_type
    return "other"


def span_type_for(event: dict[str, Any]) -> str:
    """Return the explicit v1 type or the compatible v0 name inference."""

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
    """Validate ordering, identity, and span lifecycle invariants."""

    errors: list[str] = []
    trace_id: str | None = None
    previous_seq = 0
    spans_started: set[str] = set()
    spans_ended: set[str] = set()
    root_spans: set[str] = set()
    count = 0

    for index, event in enumerate(events, start=1):
        count += 1
        if not isinstance(event, dict):
            errors.append(f"event {index}: record must be an object")
            continue

        current_trace = event.get("trace_id")
        if not isinstance(current_trace, str) or not current_trace:
            errors.append(f"event {index}: trace_id is required")
        elif trace_id is None:
            trace_id = current_trace
        elif current_trace != trace_id:
            errors.append(f"event {index}: trace_id changed")
        if expected_trace_id and current_trace != expected_trace_id:
            errors.append(f"event {index}: unexpected trace_id")

        sequence = event.get("event_seq")
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            errors.append(f"event {index}: event_seq must be an integer")
        elif sequence <= previous_seq:
            errors.append(f"event {index}: event_seq is not strictly increasing")
        else:
            previous_seq = sequence

        timestamp = event.get("ts")
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            errors.append(f"event {index}: ts must be numeric")

        kind = event.get("kind")
        if kind not in TRACE_KINDS:
            errors.append(f"event {index}: unsupported kind {kind!r}")
            continue

        if kind == "span_start":
            span_id = event.get("span_id")
            if not isinstance(span_id, str) or not span_id:
                errors.append(f"event {index}: span_start requires span_id")
            elif span_id in spans_started:
                errors.append(f"event {index}: duplicate span_start {span_id}")
            else:
                spans_started.add(span_id)
            if not isinstance(event.get("name"), str) or not event["name"]:
                errors.append(f"event {index}: span_start requires name")
            if "span_type" in event and (
                not isinstance(event["span_type"], str) or event["span_type"] not in SPAN_TYPES
            ):
                errors.append(f"event {index}: unsupported span_type {event.get('span_type')!r}")
            parent_id = event.get("parent_span_id")
            if parent_id is None:
                root_spans.add(str(span_id))
            elif not isinstance(parent_id, str) or parent_id not in spans_started:
                errors.append(f"event {index}: parent span must already exist")
            if event.get("name") == "agent.run" and expected_trial_id:
                trial_id = (event.get("attributes") or {}).get("trial_id")
                if trial_id != expected_trial_id:
                    errors.append(f"event {index}: agent.run trial_id does not match")
            if event.get("name") == "agent.run" and expected_root_attributes:
                attributes = event.get("attributes") or {}
                for key, expected in expected_root_attributes.items():
                    if attributes.get(key) != expected:
                        errors.append(f"event {index}: agent.run {key} does not match")

        elif kind == "span_end":
            span_id = event.get("span_id")
            if not isinstance(span_id, str) or not span_id:
                errors.append(f"event {index}: span_end requires span_id")
            elif span_id not in spans_started:
                errors.append(f"event {index}: span_end without span_start {span_id}")
            elif span_id in spans_ended:
                errors.append(f"event {index}: duplicate span_end {span_id}")
            else:
                spans_ended.add(span_id)
            if not isinstance(event.get("status"), str) or not event["status"]:
                errors.append(f"event {index}: span_end requires status")

        else:
            if not isinstance(event.get("name"), str) or not event["name"]:
                errors.append(f"event {index}: event requires name")
            parent_id = event.get("parent_span_id")
            if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in spans_started):
                errors.append(f"event {index}: event parent span must already exist")

    if not count:
        errors.append("trace must contain at least one event")
    if len(root_spans) > 1:
        errors.append("trace must not contain multiple root spans")
    if expected_trial_id and len(root_spans) != 1:
        errors.append("trial trace must contain exactly one root span")
    for span_id in sorted(spans_started - spans_ended):
        errors.append(f"span missing end: {span_id}")

    return TraceValidation(
        valid=not errors,
        trace_id=trace_id,
        event_count=count,
        span_count=len(spans_started),
        errors=tuple(errors),
    )


def validate_trace(
    path: str | Path,
    *,
    expected_trace_id: str | None = None,
    expected_trial_id: str | None = None,
    expected_root_attributes: dict[str, str] | None = None,
) -> TraceValidation:
    """Read and validate a JSONL trace, reporting malformed lines as errors."""

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
