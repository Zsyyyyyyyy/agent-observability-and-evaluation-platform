"""Read-only query model for the local Observability Console."""

from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Callable
from typing import Any

from regression_lab.behavior import aggregate_behavior, summarize_trial_behavior
from regression_lab.behavior_diff import snapshot_trial_behavior
from regression_lab.evolution_catalog import EvolutionCatalog


def _observed_number(value: object) -> int | float | None:
    """Keep absent evidence distinct from an observed numeric zero."""

    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


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
            behavior = summarize_trial_behavior(result)
            capabilities = behavior.get("adapter_capabilities") or {}
            tool_calls = _observed_number((scores.get("tool_integrity") or {}).get("actual", {}).get("tool_calls"))
            model_tokens = _observed_number((result.get("model_usage") or {}).get("total_tokens"))
            # Capability is the source of truth for whether a zero is observable.
            # A Black-box lifecycle Trace must be shown as N/A, never as zero usage.
            if capabilities.get("tool_trace") is not True:
                tool_calls = None
            if capabilities.get("model_usage") is not True:
                model_tokens = None
            rows.append({
                "id": result["console_id"], "trial_id": result.get("trial_id"), "agent_version": result.get("agent_version"),
                "case_id": result.get("case_id") or str(result.get("trial_id", "")).rsplit("_trial_", 1)[0],
                "agent_profile": result.get("agent_profile"), "status": result.get("status"),
                "passed": bool((result.get("evaluation") or {}).get("passed")),
                "trace_valid": bool((result.get("trace_validation") or {}).get("valid")),
                "error": result.get("error"),
                "failure_kind": (result.get("failure_attribution") or {}).get("kind"),
                "failure_reason": (result.get("failure_attribution") or {}).get("reason"),
                "failure_span": (result.get("failure_attribution") or {}).get("failure_span"),
                "failure_evidence": (result.get("failure_attribution") or {}).get("evidence"),
                "duration_ms": (scores.get("budget") or {}).get("actual", {}).get("duration_ms", 0),
                "tool_calls": tool_calls,
                "model_tokens": model_tokens,
                "changed_files": result.get("changed_files", []),
                "behavior": behavior,
                "adapter_capabilities": behavior.get("adapter_capabilities"),
                "capability_source": behavior.get("capability_source"),
                "evidence_availability": behavior.get("evidence_availability"),
                "behavior_snapshot": snapshot_trial_behavior(result),
            })
        return sorted(rows, key=lambda row: str(row["id"]))

    def trial(self, console_id: str) -> dict[str, Any] | None:
        candidate = (self.runtime_root / console_id / "result.json").resolve()
        if not candidate.is_relative_to(self.runtime_root) or not candidate.is_file():
            return None
        result = self._read_result(candidate)
        if not result:
            return None
        result["behavior"] = summarize_trial_behavior(result)
        trace_path = self._artifact_path(result.get("trace_path"), candidate.parent)
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
            trace_path = self._artifact_path(result.get("trace_path"), path.parent)
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
        def observed_average(metric: str) -> float | None:
            values = [_observed_number(row.get(metric)) for row in rows]
            observed = [float(value) for value in values if value is not None]
            return sum(observed) / len(observed) if observed else None

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
            "avg_tool_calls": observed_average("tool_calls"),
            "avg_model_tokens": observed_average("model_tokens"),
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

    def _catalog_path(self, report: dict[str, Any]) -> Path:
        value = report.get("evolution_catalog")
        return self._artifact_path(value, self.runtime_root) if isinstance(value, str) and value else self.runtime_root.parent / "evolution-catalog.json"

    @staticmethod
    def _artifact_path(value: object, base: Path) -> Path:
        path = Path(str(value or ""))
        return path if path.is_absolute() else base / path

    def context(self) -> dict[str, Any]:
        """Resolve one immutable Console scope from the Runtime Artifact."""

        report = self.latest_experiment() or {}
        raw = report.get("evaluation_context")
        agents = report.get("agents") if isinstance(report.get("agents"), list) else []
        by_label = {item.get("id"): item for item in agents if isinstance(item, dict)}
        baseline = by_label.get(report.get("baseline_id"), {})
        candidate = by_label.get(report.get("candidate_id"), {})
        if not baseline and agents:
            baseline = agents[0] if isinstance(agents[0], dict) else {}
        if not candidate and len(agents) > 1:
            candidate = agents[1] if isinstance(agents[1], dict) else {}
        fallback = {
            "schema_version": 1,
            "project_id": report.get("project_id") if isinstance(report.get("project_id"), str) else None,
            "agent_id": None,
            "experiment_id": report.get("evolution_experiment_id") if isinstance(report.get("evolution_experiment_id"), str) else None,
            "baseline_version": baseline.get("version") if isinstance(baseline.get("version"), str) else None,
            "candidate_version": candidate.get("version") if isinstance(candidate.get("version"), str) else None,
            "legacy": True,
            "scope_status": "legacy_unknown",
            "source": "legacy_artifact",
        }
        if isinstance(raw, dict):
            return {**fallback, **{field: raw.get(field, fallback[field]) for field in fallback}}

        catalog_path = self._catalog_path(report)
        if catalog_path.is_file():
            try:
                document = EvolutionCatalog(catalog_path).load()
            except ValueError:
                document = {}
            versions = {
                item.get("version") for item in (baseline, candidate)
                if isinstance(item, dict) and isinstance(item.get("version"), str)
            }
            matches = {
                item.get("agent_id") for item in document.get("versions", [])
                if isinstance(item, dict) and item.get("version") in versions and isinstance(item.get("agent_id"), str)
            }
            if len(matches) == 1:
                fallback["agent_id"] = matches.pop()
                fallback["scope_status"] = "inferred"
        return fallback

    def validate_runtime_context(self) -> dict[str, Any]:
        """Verify that Runtime facts still belong to the declared Experiment."""

        report = self.latest_experiment() or {}
        context = self.context()
        if context["legacy"]:
            return {"available": True, "context": context}

        agents = report.get("agents") if isinstance(report.get("agents"), list) else []
        by_label = {item.get("id"): item for item in agents if isinstance(item, dict)}
        baseline = by_label.get(report.get("baseline_id"), {})
        candidate = by_label.get(report.get("candidate_id"), {})
        expected = {
            "project_id": report.get("project_id"),
            "experiment_id": report.get("evolution_experiment_id"),
            "baseline_version": baseline.get("version"),
            "candidate_version": candidate.get("version"),
        }
        mismatches = [
            field for field, value in expected.items()
            if isinstance(value, str) and context.get(field) != value
        ]

        protocol = self._read_report("protocol.json") or {}
        protocol_agents = protocol.get("agents") if isinstance(protocol.get("agents"), list) else []
        observed_agent_ids = {
            item.get("agent_spec_snapshot", {}).get("agent_id")
            for item in protocol_agents if isinstance(item, dict)
            and isinstance(item.get("agent_spec_snapshot"), dict)
            and isinstance(item["agent_spec_snapshot"].get("agent_id"), str)
        }
        if context.get("agent_id") and observed_agent_ids and observed_agent_ids != {context["agent_id"]}:
            mismatches.append("agent_id")
        if mismatches:
            runtime = {
                **expected,
                "agent_id": observed_agent_ids.pop() if len(observed_agent_ids) == 1 else None,
            }
            return {
                "available": False,
                "reason": "runtime_context_mismatch",
                "context": {**context, "scope_status": "mismatch"},
                "runtime": runtime,
            }
        return {"available": True, "context": context}

    def runtime_response(self, loader: Callable[[], Any]) -> dict[str, Any]:
        """Return Runtime Evidence only after its Experiment Context is verified."""

        validation = self.validate_runtime_context()
        if not validation["available"]:
            return {
                "available": False, "reason": validation["reason"], "data": {}, "context": validation["context"],
                **({"runtime": validation["runtime"]} if "runtime" in validation else {}),
            }
        return {"available": True, "data": loader(), "context": validation["context"]}

    def evolution(self) -> dict[str, Any]:
        """Return Catalog history only when it belongs to the current Console Context."""

        report = self.latest_experiment() or {}
        context = self.context()
        catalog_path = self._catalog_path(report)
        if not catalog_path.is_file():
            return {"available": False, "reason": "catalog_missing", "context": context, "versions": [], "experiments": [], "gate_decisions": []}
        catalog = EvolutionCatalog(catalog_path)
        try:
            document = catalog.load()
        except ValueError:
            return {"available": False, "reason": "catalog_invalid", "context": context, "versions": [], "experiments": [], "gate_decisions": []}

        catalog_project = document.get("project") if isinstance(document.get("project"), dict) else {}
        if context["project_id"] and catalog_project.get("project_id") not in {None, context["project_id"]}:
            return {"available": False, "reason": "runtime_catalog_context_mismatch", "context": {**context, "scope_status": "mismatch"}, "versions": [], "experiments": [], "gate_decisions": []}

        experiment_id = context["experiment_id"]
        if isinstance(experiment_id, str):
            current = next((item for item in document["experiments"] if item.get("experiment_id") == experiment_id), None)
            if current is None:
                return {"available": False, "reason": "catalog_experiment_missing", "context": context, "versions": [], "experiments": [], "gate_decisions": []}
            version_ids = {current.get("baseline_version_id"), current.get("candidate_version_id")}
            agent_ids = {
                item.get("agent_id") for item in document["versions"]
                if item.get("version_id") in version_ids and isinstance(item.get("agent_id"), str)
            }
            if context["agent_id"] and agent_ids != {context["agent_id"]}:
                return {"available": False, "reason": "runtime_catalog_context_mismatch", "context": {**context, "scope_status": "mismatch"}, "versions": [], "experiments": [], "gate_decisions": []}
            timeline = catalog.timeline(experiment_id)
        elif isinstance(context["agent_id"], str):
            # 历史 Artifact 没有 Experiment ID 时，只展示能由同一版本对唯一定位的谱系。
            timeline = catalog.timeline(agent_id=context["agent_id"])
        else:
            return {"available": False, "reason": "legacy_context_unresolved", "context": context, "versions": [], "experiments": [], "gate_decisions": []}
        if not timeline["experiments"]:
            return {
                "available": False, "reason": "catalog_lineage_missing", "catalog_path": catalog_path.name,
                "context": context, "versions": [], "experiments": [], "gate_decisions": [],
            }
        current_experiment_id = timeline["current_experiment_id"]
        return {
            "available": True,
            "catalog_path": catalog_path.name,
            "context": context,
            "ledger_experiments": [
                item for item in timeline["experiments"]
                if item.get("experiment_id") == current_experiment_id
            ],
            **timeline,
        }

    def _read_report(self, filename: str) -> dict[str, Any] | None:
        path = self.runtime_root / filename
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None
