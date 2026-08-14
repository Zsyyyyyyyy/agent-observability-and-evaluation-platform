"""Read-only query model for the local Observability Console."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from regression_lab.behavior import aggregate_behavior
from regression_lab.evolution_catalog import EvolutionCatalog


class DashboardRepository:
    """Build browser-safe views from persisted Trial artifacts without mutating them."""

    def __init__(self, runtime_root: str | Path):
        self.runtime_root = Path(runtime_root).resolve()

    def _result_paths(self) -> list[Path]:
        if not self.runtime_root.exists():
            return []
        # Archived invalid attempts are retained for audit, but are not part
        # of the active experiment population or dashboard aggregates.
        return sorted(
            path for path in self.runtime_root.rglob("result.json")
            if "invalid-attempts" not in path.parts and "attempts" not in path.parts
        )

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
                "failure_kind": (result.get("failure_attribution") or {}).get("kind"),
                "failure_reason": (result.get("failure_attribution") or {}).get("reason"),
                "duration_ms": (scores.get("budget") or {}).get("actual", {}).get("duration_ms", 0),
                "tool_calls": (scores.get("tool_integrity") or {}).get("actual", {}).get("tool_calls", 0),
                "model_tokens": (result.get("model_usage") or {}).get("total_tokens", 0),
                "changed_files": result.get("changed_files", []),
                "behavior": result.get("behavior"),
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

    def policy_stop_evidence(self) -> dict[str, Any]:
        """Summarize the V4.1 verification-stop invariant from selected Traces.

        This is a read-only behavioral claim: a completed test is only counted
        when its Trace records the deterministic policy stop and no later model
        or tool span is started.
        """

        candidates = 0
        verification_passed = 0
        policy_stops = 0
        missing_policy_stops = 0
        post_stop_calls = 0
        affected_cases: set[str] = set()
        for path in self._result_paths():
            result = self._read_result(path)
            if not result or result.get("agent_profile") != "bounded-success-stop-verify-v4-1":
                continue
            candidates += 1
            if result.get("agent_exit_reason") == "verification_passed":
                verification_passed += 1
            trace_path = Path(str(result.get("trace_path", "")))
            if not trace_path.is_file():
                missing_policy_stops += 1
                continue
            events: list[dict[str, Any]] = []
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
            stops = [
                event for event in events
                if event.get("kind") == "event" and event.get("name") == "agent.stop"
                and isinstance(event.get("attributes"), dict)
                and event["attributes"].get("reason") == "verification_passed_policy"
            ]
            if len(stops) != 1 or not isinstance(stops[0].get("event_seq"), int):
                missing_policy_stops += 1
                continue
            policy_stops += 1
            stop_seq = stops[0]["event_seq"]
            violations = [
                event for event in events
                if isinstance(event.get("event_seq"), int) and event["event_seq"] > stop_seq
                and event.get("kind") == "span_start" and event.get("name") in {"model.call", "tool.call"}
            ]
            post_stop_calls += len(violations)
            if violations:
                affected_cases.add(str(result.get("trial_id", "")).rsplit("_trial_", 1)[0])
        return {
            "available": candidates > 0,
            "candidate_trial_count": candidates,
            "verification_passed_count": verification_passed,
            "policy_stop_trace_count": policy_stops,
            "missing_policy_stop_count": missing_policy_stops,
            "post_stop_model_or_tool_spans": post_stop_calls,
            "affected_cases": sorted(case for case in affected_cases if case),
        }

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
            "behavior": aggregate_behavior([
                {
                    "status": row["status"], "evaluation_passed": row["passed"],
                    "test_passed": row["passed"], "behavior": row.get("behavior"),
                }
                for row in rows
            ]),
        }

    def latest_experiment(self) -> dict[str, Any] | None:
        return self._read_report("experiment.json")

    def latest_gate(self) -> dict[str, Any] | None:
        # Positive comparisons conventionally write gate-report.json, while a
        # deliberately rejected negative control writes gate-negative.json.
        # The Console is read-only and should render either formal decision.
        return self._read_report("gate-report.json") or self._read_report("gate-negative.json")

    def protocol(self) -> dict[str, Any]:
        """Return a browser-safe protocol summary without environment secrets."""

        raw = self._read_report("protocol.json")
        if not raw:
            return {"available": False, "level": "legacy_unverified"}
        benchmark = raw.get("benchmark") if isinstance(raw.get("benchmark"), dict) else {}
        cases = benchmark.get("cases") if isinstance(benchmark.get("cases"), list) else []
        model = raw.get("model") if isinstance(raw.get("model"), dict) else {}
        sandbox = raw.get("sandbox") if isinstance(raw.get("sandbox"), dict) else {}
        execution = raw.get("execution") if isinstance(raw.get("execution"), dict) else {}
        experiment = self.latest_experiment() or {}
        protocol_state = experiment.get("protocol") if isinstance(experiment.get("protocol"), dict) else {}
        return {
            "available": True,
            "fingerprint": raw.get("protocol_fingerprint"),
            "comparability": protocol_state.get("comparability") or {"level": "not_available"},
            "comparison_intent": raw.get("comparison_intent"),
            "allowed_differences": raw.get("allowed_differences", []),
            "case_count": len(cases), "model": model.get("model"), "provider": model.get("provider"),
            "trials_per_case": execution.get("trials_per_case"), "schedule_seed": execution.get("schedule_seed"),
            "docker": sandbox.get("docker"), "image": sandbox.get("image"),
        }

    def evolution(self) -> dict[str, Any]:
        """Return the Timeline subset for the experiment currently open in Console."""

        report = self.latest_experiment() or {}
        catalog_path = report.get("evolution_catalog")
        if not isinstance(catalog_path, str) or not catalog_path:
            catalog_path = str(self.runtime_root.parent / "evolution-catalog.json")
        experiment_id = report.get("evolution_experiment_id")
        catalog = EvolutionCatalog(catalog_path)
        try:
            catalog.load()
        except ValueError:
            return {"available": False, "reason": "catalog_invalid", "versions": [], "experiments": [], "gate_decisions": []}
        if not Path(catalog_path).exists():
            return {"available": False, "reason": "catalog_missing", "versions": [], "experiments": [], "gate_decisions": []}
        timeline = catalog.timeline(experiment_id if isinstance(experiment_id, str) else None)
        return {
            "available": bool(timeline["experiments"]), "catalog_path": Path(catalog_path).name,
            **timeline,
        }

    def _read_report(self, filename: str) -> dict[str, Any] | None:
        path = self.runtime_root / filename
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None
