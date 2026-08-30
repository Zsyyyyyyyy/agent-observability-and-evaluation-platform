"""Deterministic structural comparison for two persisted Trace artifacts."""

from __future__ import annotations

from typing import Any


def _spans(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    spans: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("kind") == "span_start" and isinstance(event.get("span_id"), str):
            spans[event["span_id"]] = {"id": event["span_id"], "parent": event.get("parent_span_id"), "name": event.get("name"), "span_type": event.get("span_type"), "start": event.get("ts"), "end": None}
        elif event.get("kind") == "span_end" and isinstance(event.get("span_id"), str) and event["span_id"] in spans:
            spans[event["span_id"]]["end"] = event.get("ts")
    return spans


def _signature(span: dict[str, Any]) -> tuple[str, str]:
    return str(span.get("span_type") or "other"), str(span.get("name") or "unknown")


def _lcs(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[tuple[int, int]]:
    table = [[0] * (len(right) + 1) for _ in range(len(left) + 1)]
    for i in range(len(left) - 1, -1, -1):
        for j in range(len(right) - 1, -1, -1):
            table[i][j] = 1 + table[i + 1][j + 1] if _signature(left[i]) == _signature(right[j]) else max(table[i + 1][j], table[i][j + 1])
    pairs: list[tuple[int, int]] = []
    i = j = 0
    while i < len(left) and j < len(right):
        if _signature(left[i]) == _signature(right[j]):
            pairs.append((i, j)); i += 1; j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def _critical_path(spans: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """按嵌套 Span 的最长墙钟路径展示诊断路径；并行重叠时明确标为近似。"""

    children: dict[str | None, list[dict[str, Any]]] = {}
    for span in spans.values():
        children.setdefault(span["parent"], []).append(span)
    def duration(span: dict[str, Any]) -> float:
        start, end = span.get("start"), span.get("end")
        return float(end - start) if isinstance(start, (int, float)) and isinstance(end, (int, float)) else 0.0
    def longest(parent: str | None) -> list[dict[str, Any]]:
        options = children.get(parent, [])
        if not options:
            return []
        selected = max(options, key=duration)
        return [selected, *longest(selected["id"])]
    chain = longest(None)
    return {"method": "nested_span_wall_clock", "precision": "approximate", "span_ids": [span["id"] for span in chain], "duration_ms": round(duration(chain[0]) * 1000, 3) if chain else None}


def compare_traces(baseline_events: list[dict[str, Any]], candidate_events: list[dict[str, Any]]) -> dict[str, Any]:
    """以名称/类型对齐 Span；不使用随机 span_id，保证 Artifact 可复现。"""

    baseline, candidate = _spans(baseline_events), _spans(candidate_events)
    def children(spans: dict[str, dict[str, Any]], parent: str | None) -> list[dict[str, Any]]:
        return sorted((span for span in spans.values() if span["parent"] == parent), key=lambda span: (span.get("start") is None, span.get("start"), span["id"]))

    rows: list[dict[str, Any]] = []
    first_divergence: dict[str, Any] | None = None
    def visit(left_parent: str | None, right_parent: str | None, depth: int) -> None:
        nonlocal first_divergence
        left, right = children(baseline, left_parent), children(candidate, right_parent)
        pairs = _lcs(left, right)
        paired_left, paired_right = {i for i, _ in pairs}, {j for _, j in pairs}
        for index, span in enumerate(left):
            if index not in paired_left:
                row = {"kind": "removed", "depth": depth, "baseline": {"span_id": span["id"], "name": span["name"], "span_type": span["span_type"]}, "candidate": None}
                rows.append(row); first_divergence = first_divergence or row
        for index, span in enumerate(right):
            if index not in paired_right:
                row = {"kind": "added", "depth": depth, "baseline": None, "candidate": {"span_id": span["id"], "name": span["name"], "span_type": span["span_type"]}}
                rows.append(row); first_divergence = first_divergence or row
        for left_index, right_index in pairs:
            before, after = left[left_index], right[right_index]
            rows.append({"kind": "matched", "depth": depth, "baseline": {"span_id": before["id"], "name": before["name"], "span_type": before["span_type"]}, "candidate": {"span_id": after["id"], "name": after["name"], "span_type": after["span_type"]}})
            visit(before["id"], after["id"], depth + 1)
    visit(None, None, 0)
    return {"schema_version": 1, "alignment": "ordered_sibling_lcs", "rows": rows, "first_divergence": first_divergence, "matched_span_count": sum(row["kind"] == "matched" for row in rows), "critical_path": {"baseline": _critical_path(baseline), "candidate": _critical_path(candidate)}}
