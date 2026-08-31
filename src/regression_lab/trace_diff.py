"""Deterministic structural comparison for two persisted Trace artifacts."""

from __future__ import annotations

from typing import Any


def _spans(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    spans: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("kind") == "span_start" and isinstance(event.get("span_id"), str):
            spans[event["span_id"]] = {"id": event["span_id"], "parent": event.get("parent_span_id"), "name": event.get("name"), "span_type": event.get("span_type"), "start_sequence": event.get("event_seq"), "start": event.get("ts"), "end": None, "status": None, "attributes": event.get("attributes") if isinstance(event.get("attributes"), dict) else {}, "end_attributes": {}}
        elif event.get("kind") == "span_end" and isinstance(event.get("span_id"), str) and event["span_id"] in spans:
            spans[event["span_id"]]["end"] = event.get("ts")
            spans[event["span_id"]]["status"] = event.get("status")
            spans[event["span_id"]]["end_attributes"] = event.get("attributes") if isinstance(event.get("attributes"), dict) else {}
    return spans


def _signature(span: dict[str, Any]) -> tuple[str, ...]:
    span_type, name = str(span.get("span_type") or "other"), str(span.get("name") or "unknown")
    attributes = span["attributes"]
    if span_type == "tool":
        return span_type, name, str(attributes.get("tool_name") or "unknown")
    if span_type == "llm" and attributes.get("model") not in {None, "unknown"}:
        return span_type, name, str(attributes["model"])
    return span_type, name


def _operation_site(span: dict[str, Any]) -> tuple[str, str]:
    return str(span.get("span_type") or "other"), str(span.get("name") or "unknown")


def _span_label(span: dict[str, Any]) -> str:
    attributes = span["attributes"]
    if span.get("span_type") == "tool" and attributes.get("tool_name") not in {None, "unknown"}:
        return f"{span['name']}({attributes['tool_name']})"
    if span.get("span_type") == "llm" and attributes.get("model") not in {None, "unknown"}:
        return f"{span['name']}({attributes['model']})"
    return str(span.get("name") or "unknown")


def _matched_divergence(before: dict[str, Any], after: dict[str, Any]) -> tuple[str, str] | None:
    before_tool, after_tool = before["attributes"].get("tool_name"), after["attributes"].get("tool_name")
    if before_tool != after_tool:
        return "tool_changed", "Tool operation changed"
    before_model, after_model = before["attributes"].get("model"), after["attributes"].get("model")
    if before_model not in {None, "unknown"} and after_model not in {None, "unknown"} and before_model != after_model:
        return "model_changed", "Model identity changed"
    before_status, after_status = before.get("status"), after.get("status")
    if isinstance(before_status, str) and isinstance(after_status, str) and before_status != after_status:
        return "status_changed", "Span outcome changed"
    return None


def _view(span: dict[str, Any]) -> dict[str, Any]:
    """只输出展示差异所需的聚合值，不暴露模型正文或工具参数。"""

    start, end = span.get("start"), span.get("end")
    usage = span.get("end_attributes", {}).get("usage")
    usage = usage if isinstance(usage, dict) else {}
    tokens = usage.get("total_tokens")
    return {
        "span_id": span["id"], "name": span["name"], "span_type": span["span_type"], "status": span["status"],
        "duration_ms": round((float(end) - float(start)) * 1000, 3) if isinstance(start, (int, float)) and isinstance(end, (int, float)) else None,
        "tokens": tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None,
        "tool_calls": 1 if span.get("name") == "tool.call" else 0,
    }


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
        return sorted((span for span in spans.values() if span["parent"] == parent), key=lambda span: (not isinstance(span.get("start_sequence"), int), span.get("start_sequence") if isinstance(span.get("start_sequence"), int) else span.get("start"), span["id"]))

    rows: list[dict[str, Any]] = []
    first_divergence: dict[str, Any] | None = None
    row_number = 0
    row_paths: dict[str, list[str]] = {}

    def record_divergence(kind: str, row: dict[str, Any], reason: str) -> None:
        nonlocal first_divergence
        if first_divergence is not None:
            return
        first_divergence = {
            "row_id": row["row_id"], "kind": kind, "depth": row["depth"], "path": row["path"],
            "baseline": {key: row["baseline"].get(key) for key in ("span_id", "status")} if row["baseline"] else None,
            "candidate": {key: row["candidate"].get(key) for key in ("span_id", "status")} if row["candidate"] else None,
            "reason": reason,
        }

    def emit_row(
        kind: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        parent_row_id: str | None,
        depth: int,
    ) -> dict[str, Any]:
        nonlocal row_number
        row_number += 1
        before_view = _view(before) if before is not None else None
        after_view = _view(after) if after is not None else None
        row = {
            "row_id": f"row-{row_number:04d}",
            "parent_row_id": parent_row_id,
            "kind": kind,
            "depth": depth,
            "baseline": before_view,
            "candidate": after_view,
        }
        row["path"] = [*row_paths.get(parent_row_id or "", []), _span_label(before or after)]
        row_paths[row["row_id"]] = row["path"]
        if kind == "matched":
            row["delta"] = {
                key: after_view[key] - before_view[key]
                if isinstance(before_view[key], (int, float)) and isinstance(after_view[key], (int, float))
                else None
                for key in ("duration_ms", "tokens", "tool_calls")
            }
        rows.append(row)
        return row

    def emit_one_sided_subtree(
        span: dict[str, Any],
        *,
        kind: str,
        parent_row_id: str | None,
        depth: int,
        spans: dict[str, dict[str, Any]],
    ) -> None:
        row = emit_row(kind, span if kind == "removed" else None, span if kind == "added" else None, parent_row_id, depth)
        record_divergence(f"span_{kind}", row, f"Span {kind}")
        for child in children(spans, span["id"]):
            emit_one_sided_subtree(child, kind=kind, parent_row_id=row["row_id"], depth=depth + 1, spans=spans)

    def emit_matched_pair(
        before: dict[str, Any],
        after: dict[str, Any],
        parent_row_id: str | None,
        depth: int,
    ) -> dict[str, Any]:
        row = emit_row("matched", before, after, parent_row_id, depth)
        divergence = _matched_divergence(before, after)
        if divergence:
            row["divergence"] = divergence[0]
            if divergence[0] in {"tool_changed", "model_changed"}:
                # 操作替换时展示 Candidate 的实际执行身份，便于定位新版本行为。
                row["path"][-1] = _span_label(after)
                row_paths[row["row_id"]] = row["path"]
            if divergence[0] != "status_changed":
                record_divergence(divergence[0], row, divergence[1])

        # 状态是上游工具/模型或子节点分叉的结果，应让更具体的原因优先占据首分叉。
        visit(before["id"], after["id"], row["row_id"], depth + 1)
        if divergence and divergence[0] == "status_changed":
            record_divergence(divergence[0], row, divergence[1])
        return row

    def emit_unmatched_segment(
        left: list[dict[str, Any]],
        right: list[dict[str, Any]],
        parent_row_id: str | None,
        depth: int,
    ) -> None:
        left_position = right_position = 0
        while left_position < len(left) or right_position < len(right):
            before = left[left_position] if left_position < len(left) else None
            after = right[right_position] if right_position < len(right) else None
            # LCS 以操作身份对齐；同一调用位置换了工具或模型时，额外保留为一条行为替换。
            if before and after and _operation_site(before) == _operation_site(after):
                emit_matched_pair(before, after, parent_row_id, depth)
                left_position += 1
                right_position += 1
            elif before:
                emit_one_sided_subtree(before, kind="removed", parent_row_id=parent_row_id, depth=depth, spans=baseline)
                left_position += 1
            else:
                emit_one_sided_subtree(after, kind="added", parent_row_id=parent_row_id, depth=depth, spans=candidate)
                right_position += 1

    def visit(
        left_parent: str | None,
        right_parent: str | None,
        parent_row_id: str | None,
        depth: int,
    ) -> None:
        left, right = children(baseline, left_parent), children(candidate, right_parent)
        pairs = _lcs(left, right)
        left_position = right_position = 0
        for left_index, right_index in pairs:
            emit_unmatched_segment(left[left_position:left_index], right[right_position:right_index], parent_row_id, depth)
            before, after = left[left_index], right[right_index]
            emit_matched_pair(before, after, parent_row_id, depth)
            left_position, right_position = left_index + 1, right_index + 1
        emit_unmatched_segment(left[left_position:], right[right_position:], parent_row_id, depth)

    visit(None, None, None, 0)
    return {"schema_version": 2, "alignment": "ordered_sibling_lcs", "ordering": {"method": "aligned_causal_preorder", "parallel_precision": "partial_order"}, "rows": rows, "first_divergence": first_divergence, "matched_span_count": sum(row["kind"] == "matched" for row in rows), "critical_path": {"baseline": _critical_path(baseline), "candidate": _critical_path(candidate)}}
