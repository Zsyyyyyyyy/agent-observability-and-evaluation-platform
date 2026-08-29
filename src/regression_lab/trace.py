"""无外部依赖的 JSONL Trace 采集器。"""

from __future__ import annotations

from regression_lab_observer.trace import JsonlTraceWriter, SPAN_TYPES


class TraceCollector(JsonlTraceWriter):
    """兼容原有平台接口的 Trace Writer 名称。"""
