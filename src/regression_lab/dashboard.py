"""Read-only query model for the local Observability Console."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class DashboardRepository:
    """Build browser-safe views from persisted Trial artifacts without mutating them."""

    def __init__(self, runtime_root: str | Path):
        self.runtime_root = Path(runtime_root).resolve()

    def _result_paths(self) -> list[Path]:
        if not self.runtime_root.exists():
            return []
        # Archived invalid attempts are retained for audit, but are not part
        # of the active experiment population or dashboard aggregates.
        return sorted(path for path in self.runtime_root.rglob("result.json") if "invalid-attempts" not in path.parts)

    def _read_result(self, path: Path) -> dict[str, Any] | None:
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(result, dict):
            return None
        result["console_id"] = str(path.parent.relative_to(self.runtime_root))
        return result

    def trials(self) -> list[dict[str, Any]]:
        rows = []
        for path in self._result_paths():
            result = self._read_result(path)
            if not result:
                continue
            scores = {item.get("evaluator"): item for item in result.get("scores", []) if isinstance(item, dict)}
            rows.append({
                "id": result["console_id"], "trial_id": result.get("trial_id"), "agent_version": result.get("agent_version"),
                "case_id": result.get("case_id") or str(result.get("trial_id", "")).rsplit("_trial_", 1)[0],
                "agent_profile": result.get("agent_profile"), "status": result.get("status"),
                "passed": bool((result.get("evaluation") or {}).get("passed")),
                "trace_valid": bool((result.get("trace_validation") or {}).get("valid")),
                "error": result.get("error"),
                "duration_ms": (scores.get("budget") or {}).get("actual", {}).get("duration_ms", 0),
                "tool_calls": (scores.get("tool_integrity") or {}).get("actual", {}).get("tool_calls", 0),
                "model_tokens": (result.get("model_usage") or {}).get("total_tokens", 0),
                "changed_files": result.get("changed_files", []),
            })
        return sorted(rows, key=lambda row: str(row["id"]))

    def trial(self, console_id: str) -> dict[str, Any] | None:
        candidate = (self.runtime_root / console_id / "result.json").resolve()
        if not candidate.is_relative_to(self.runtime_root) or not candidate.is_file():
            return None
        result = self._read_result(candidate)
        if not result:
            return None
        trace_path = Path(str(result.get("trace_path", "")))
        events = []
        if trace_path.is_file():
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
        return {"result": result, "trace": events}

    def dashboard(self) -> dict[str, Any]:
        rows = self.trials()
        count = len(rows) or 1
        return {
            "runtime_label": f"Local experiment artifacts · {self.runtime_root.name}",
            "trial_count": len(rows),
            "passed_count": sum(bool(row["passed"]) for row in rows),
            "pass_rate": sum(bool(row["passed"]) for row in rows) / count if rows else 0.0,
            "model_failed_count": sum(row["status"] == "model_failed" for row in rows),
            "model_failed_rate": sum(row["status"] == "model_failed" for row in rows) / count if rows else 0.0,
            "trace_incomplete_count": sum(row["status"] == "trace_incomplete" for row in rows),
            "trace_incomplete_rate": sum(row["status"] == "trace_incomplete" for row in rows) / count if rows else 0.0,
            "avg_duration_ms": sum(float(row["duration_ms"] or 0) for row in rows) / count if rows else 0.0,
            "avg_tool_calls": sum(float(row["tool_calls"] or 0) for row in rows) / count if rows else 0.0,
            "avg_model_tokens": sum(float(row["model_tokens"] or 0) for row in rows) / count if rows else 0.0,
        }

    def latest_experiment(self) -> dict[str, Any] | None:
        return self._read_report("experiment.json")

    def latest_gate(self) -> dict[str, Any] | None:
        return self._read_report("gate-report.json")

    def _read_report(self, filename: str) -> dict[str, Any] | None:
        path = self.runtime_root / filename
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None
