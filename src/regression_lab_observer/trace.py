"""只依赖标准库的 Regression Lab JSONL Trace 写入器。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Lock
from typing import Any


SPAN_TYPES = {
    "agent",
    "llm",
    "tool",
    "test",
    "retrieval",
    "context",
    "workflow",
    "mcp",
    "other",
}


class JsonlTraceWriter:
    """向一个 Trial 独占的 JSONL 文件顺序追加 Trace 事件。"""

    def __init__(self, output: str | Path, trace_id: str):
        self.output = Path(output)
        self.output.parent.mkdir(parents=True, exist_ok=True)
        # 每个 Attempt 独占路径；清空旧文件可避免恢复运行误用历史证据。
        self.output.write_text("", encoding="utf-8")
        self.trace_id = trace_id
        self._seq = 0
        self._span_seq = 0
        self._lock = Lock()

    def _write(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            payload = {"trace_id": self.trace_id, "event_seq": self._seq, "ts": time.time(), **event}
            with self.output.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
            return payload

    def start_span(self, name: str, parent_id: str | None = None, *, span_type: str = "other", **attrs: Any) -> str:
        if span_type not in SPAN_TYPES:
            raise ValueError(f"unsupported span_type: {span_type!r}")
        with self._lock:
            self._span_seq += 1
            span_id = f"span_{self._span_seq:04d}"
        self._write({
            "kind": "span_start", "span_id": span_id, "parent_span_id": parent_id,
            "name": name, "span_type": span_type, "attributes": attrs,
        })
        return span_id

    def end_span(self, span_id: str, status: str = "ok", **attrs: Any) -> None:
        self._write({"kind": "span_end", "span_id": span_id, "status": status, "attributes": attrs})

    def event(self, name: str, parent_id: str | None = None, **attrs: Any) -> None:
        self._write({"kind": "event", "name": name, "parent_span_id": parent_id, "attributes": attrs})

    def summary(self) -> dict[str, Any]:
        events = []
        if self.output.exists():
            for line in self.output.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        starts = {event["span_id"] for event in events if event.get("kind") == "span_start"}
        ends = {event["span_id"] for event in events if event.get("kind") == "span_end"}
        return {
            "trace_id": self.trace_id,
            "event_count": len(events),
            "span_count": len(starts),
            "closed_span_count": len(starts & ends),
            "open_span_count": len(starts - ends),
            "names": [event.get("name") for event in events if event.get("name")],
            "status": "complete" if starts == ends else "incomplete",
        }
