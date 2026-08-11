"""Small JSONL trace collector used by the Day 2 smoke harness.

This is intentionally dependency-free. The later Phoenix exporter can consume
the same event shape without forcing the first smoke test to install an
observability stack.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Lock
from typing import Any


class TraceCollector:
    """Write ordered, append-only trace events to a JSONL file."""

    def __init__(self, output: str | Path, trace_id: str):
        self.output = Path(output)
        self.output.parent.mkdir(parents=True, exist_ok=True)
        # A Trial owns its trace file.  Truncating it prevents a crashed or
        # resumed process from having an old, valid trace accepted as its own.
        self.output.write_text("", encoding="utf-8")
        self.trace_id = trace_id
        self._seq = 0
        self._span_seq = 0
        self._lock = Lock()

    def _write(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            payload = {
                "trace_id": self.trace_id,
                "event_seq": self._seq,
                "ts": time.time(),
                **event,
            }
            with self.output.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
            return payload

    def start_span(self, name: str, parent_id: str | None = None, **attrs: Any) -> str:
        with self._lock:
            self._span_seq += 1
            span_id = f"span_{self._span_seq:04d}"
        self._write({
            "kind": "span_start",
            "span_id": span_id,
            "parent_span_id": parent_id,
            "name": name,
            "attributes": attrs,
        })
        return span_id

    def end_span(self, span_id: str, status: str = "ok", **attrs: Any) -> None:
        self._write({
            "kind": "span_end",
            "span_id": span_id,
            "status": status,
            "attributes": attrs,
        })

    def event(self, name: str, parent_id: str | None = None, **attrs: Any) -> None:
        self._write({
            "kind": "event",
            "name": name,
            "parent_span_id": parent_id,
            "attributes": attrs,
        })

    def summary(self) -> dict[str, Any]:
        events = []
        if self.output.exists():
            for line in self.output.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        starts = {e["span_id"] for e in events if e.get("kind") == "span_start"}
        ends = {e["span_id"] for e in events if e.get("kind") == "span_end"}
        names = [e.get("name") for e in events if e.get("name")]
        return {
            "trace_id": self.trace_id,
            "event_count": len(events),
            "span_count": len(starts),
            "closed_span_count": len(starts & ends),
            "open_span_count": len(starts - ends),
            "names": names,
            "status": "complete" if starts == ends else "incomplete",
        }
