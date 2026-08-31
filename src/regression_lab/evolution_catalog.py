"""Persistent, append-safe index over immutable Agent experiment Artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from regression_lab.evolution import EVOLUTION_SCHEMA_VERSION, evaluation_context_hash, validate_evolution_document


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _identifier(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:20]}"


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return _hash({"missing": str(path)})


def _kind(adapter_id: str) -> str:
    return {"external-command": "external", "react-agent": "react", "readonly-replay": "replay"}.get(adapter_id, "custom")


def _agent_id(version: str) -> str:
    base = re.sub(r"[-_]v\d+(?:[._-].*)?$", "", version.lower())
    base = re.sub(r"[^a-z0-9_-]+", "-", base).strip("-_")
    return base[:64] or "custom-agent"


def _attempt_status(status: Any) -> str:
    value = str(status or "infra_failed")
    return {"invalid": "trace_incomplete", "aborted": "cancelled", "running": "cancelled"}.get(value, value if value in {
        "queued", "completed", "timed_out", "model_failed", "agent_failed", "infra_failed", "trace_incomplete", "environment_mismatch", "cancelled"
    } else "infra_failed")


_COMPARISON_METRICS = (
    "evaluation_pass_rate",
    "avg_duration_ms",
    "avg_model_tokens",
    "avg_tool_calls",
)


def _number(value: Any) -> float | None:
    """Return finite numeric evidence without coercing absent values to zero."""

    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _comparison_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Persist the release-decision metrics next to, not inside, source artifacts."""

    comparison = report.get("comparison") if isinstance(report.get("comparison"), dict) else {}
    summary: dict[str, Any] = {"baseline": {}, "candidate": {}, "delta": {}}
    for side in ("baseline", "candidate"):
        source = comparison.get(side) if isinstance(comparison.get(side), dict) else {}
        summary[side] = {metric: value for metric in _COMPARISON_METRICS
                         if (value := _number(source.get(metric))) is not None}
    for metric in _COMPARISON_METRICS:
        baseline = summary["baseline"].get(metric)
        candidate = summary["candidate"].get(metric)
        if baseline is not None and candidate is not None:
            summary["delta"][metric] = candidate - baseline
    return summary


def _comparison_basis(*, case_ids: list[str], cases: dict[str, dict[str, Any]], report: dict[str, Any]) -> dict[str, Any]:
    """Fingerprint the benchmark protocol independently of the Agent versions."""

    return {
        "case_ids": case_ids,
        "case_fingerprints": [
            {
                "case_id": case_id,
                "fixture_hash": cases.get(case_id, {}).get("fixture_hash"),
                "test_hash": cases.get(case_id, {}).get("test_hash"),
                "policy_hash": cases.get(case_id, {}).get("policy_hash"),
            }
            for case_id in case_ids
        ],
        "metrics_version": report.get("metrics_version"),
        "evaluator_version": f"metrics-v{report.get('metrics_version', 2)}",
        "trial_count_required_per_case": report.get("trial_count_required_per_case"),
    }


def _comparability(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, str]:
    """Classify whether two release comparisons can support a trend claim."""

    if previous is None:
        return {"level": "first", "reason": "First recorded experiment for this Agent lineage."}
    before = previous.get("comparison_basis")
    after = current.get("comparison_basis")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {"level": "none", "reason": "Protocol fingerprints are unavailable for this historical comparison."}
    if previous.get("comparison_basis_hash") == current.get("comparison_basis_hash"):
        return {"level": "strict", "reason": "Same Cases, fixture/tests, tool policy, evaluator, and repeat count."}
    before_cases = {case_id for case_id in before.get("case_ids", []) if isinstance(case_id, str)}
    after_cases = {case_id for case_id in after.get("case_ids", []) if isinstance(case_id, str)}
    overlap = len(before_cases & after_cases)
    evaluator_matches = before.get("evaluator_version") == after.get("evaluator_version")
    if overlap and evaluator_matches:
        return {"level": "partial", "reason": f"{overlap} overlapping Case(s); benchmark protocol changed."}
    return {"level": "none", "reason": "No shared benchmark basis; do not read this as a metric trend."}


def _lineage_order(versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order explicit parent chains before timestamp-only historical records."""

    by_id = {row.get("version_id"): row for row in versions if isinstance(row.get("version_id"), str)}
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(version_id: str) -> None:
        if version_id in seen or version_id not in by_id:
            return
        parent = by_id[version_id].get("parent_version_id")
        if isinstance(parent, str):
            visit(parent)
        seen.add(version_id)
        ordered.append(by_id[version_id])

    for row in sorted(versions, key=lambda item: (str(item.get("created_at") or ""), str(item.get("version") or ""))):
        version_id = row.get("version_id")
        if isinstance(version_id, str):
            visit(version_id)
    return ordered


class EvolutionCatalog:
    """A validated JSON catalog; source Artifacts remain outside this document."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()

    @staticmethod
    def empty() -> dict[str, Any]:
        return {
            "schema_version": EVOLUTION_SCHEMA_VERSION,
            "agents": [], "versions": [], "cases": [], "experiments": [],
            "trials": [], "attempts": [], "gate_decisions": [],
        }

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self.empty()
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid evolution catalog: {exc}") from exc
        validation = validate_evolution_document(document)
        if not validation.valid:
            raise ValueError("invalid evolution catalog: " + "; ".join(validation.errors))
        return document

    def save(self, document: dict[str, Any]) -> None:
        validation = validate_evolution_document(document)
        if not validation.valid:
            raise ValueError("refusing to save invalid evolution catalog: " + "; ".join(validation.errors))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    @staticmethod
    def _upsert(document: dict[str, Any], collection: str, id_field: str, payload: dict[str, Any]) -> None:
        rows = document[collection]
        for index, row in enumerate(rows):
            if row.get(id_field) == payload[id_field]:
                rows[index] = payload
                return
        rows.append(payload)

    def index_experiment(self, report: dict[str, Any], *, artifact_root: str | Path,
                         manifests: Iterable[dict[str, Any]] = (), project_id: str | None = None) -> str:
        """Index an experiment report and its selected Attempt evidence idempotently."""

        document = self.load()
        if project_id is not None:
            existing_project = document.get("project")
            if existing_project is None:
                document["project"] = {"project_id": project_id}
            elif not isinstance(existing_project, dict) or existing_project.get("project_id") != project_id:
                raise ValueError("Evolution Catalog project_id does not match this Experiment")
        root = Path(artifact_root).resolve()
        protocol_path = root / "protocol.json"
        try:
            protocol_artifact = json.loads(protocol_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            protocol_artifact = {}
        if not isinstance(protocol_artifact, dict):
            protocol_artifact = {}
        protocol_agents = {
            str(item.get("label")): item for item in protocol_artifact.get("agents", [])
            if isinstance(item, dict) and isinstance(item.get("label"), str)
        }
        agents = report.get("agents") or []
        if not isinstance(agents, list) or len(agents) < 2:
            raise ValueError("experiment report must contain baseline and candidate agents")
        manifest_by_id = {item.get("id"): item for item in manifests if isinstance(item, dict) and isinstance(item.get("id"), str)}
        case_ids = sorted({str(job.get("case_id")) for summary in (report.get("summaries") or {}).values() if isinstance(summary, dict)
                           for job in summary.get("jobs", []) if isinstance(job, dict) and isinstance(job.get("case_id"), str)})
        experiment_id = _identifier("exp", {"artifact_root": str(root), "agents": agents, "cases": case_ids})
        def stable_agent_id(agent: dict[str, Any]) -> str:
            protocol_agent = protocol_agents.get(str(agent.get("id")), {})
            snapshot = protocol_agent.get("agent_spec_snapshot") if isinstance(protocol_agent, dict) else None
            if isinstance(snapshot, dict) and isinstance(snapshot.get("agent_id"), str):
                return snapshot["agent_id"]
            return _agent_id(str(agent.get("version", "")))

        observed_agent_ids = {stable_agent_id(agent) for agent in agents[:2] if isinstance(agent, dict)}
        lineage_agent_id = observed_agent_ids.pop() if len(observed_agent_ids) == 1 else None
        context = {
            "project_id": project_id,
            "case_ids": case_ids,
            "agents": [{"id": item.get("id"), "version": item.get("version")} for item in agents],
            "metrics_version": report.get("metrics_version"),
            "trial_count_required_per_case": report.get("trial_count_required_per_case"),
            "protocol_fingerprint": (report.get("protocol") or {}).get("fingerprint") if isinstance(report.get("protocol"), dict) else None,
            "protocol_comparability": (report.get("protocol") or {}).get("comparability") if isinstance(report.get("protocol"), dict) else None,
            # 保留既有比较上下文，同时写入 Console 用的稳定身份，避免前端从版本名猜测作用域。
            "identity": {
                "schema_version": 1,
                "project_id": project_id,
                "agent_id": lineage_agent_id,
                "experiment_id": experiment_id,
                "baseline_version": agents[0].get("version"),
                "candidate_version": agents[1].get("version"),
            },
        }
        existing_experiment = next((row for row in document["experiments"] if row.get("experiment_id") == experiment_id), {})
        created_at = str(existing_experiment.get("created_at") or _now())

        for case_id in case_ids:
            manifest = manifest_by_id.get(case_id, {})
            manifest_path = Path(str(manifest.get("_manifest_path", "")))
            policy = manifest.get("tool_policy", {}) if isinstance(manifest.get("tool_policy"), dict) else {}
            task = manifest.get("task", {}) if isinstance(manifest.get("task"), dict) else {}
            fixture = manifest.get("fixture", {}) if isinstance(manifest.get("fixture"), dict) else {}
            self._upsert(document, "cases", "case_id", {
                "case_id": case_id, "manifest_id": case_id,
                "manifest_version": int(manifest.get("version", 1) or 1),
                "fixture_hash": _hash(fixture), "test_hash": _hash({"test_command": fixture.get("test_command"), "task": task.get("expected_behavior")}),
                "policy_hash": _hash(policy), "manifest_path": str(manifest_path), "manifest_hash": _file_hash(manifest_path),
            })

        cases_by_id = {row["case_id"]: row for row in document["cases"]}
        comparison_basis = _comparison_basis(case_ids=case_ids, cases=cases_by_id, report=report)

        version_by_agent_label: dict[str, str] = {}
        for position, agent in enumerate(agents[:2]):
            if not isinstance(agent, dict) or not isinstance(agent.get("id"), str) or not isinstance(agent.get("version"), str):
                raise ValueError("experiment agents require id and version")
            label, version = agent["id"], agent["version"]
            summary = (report.get("summaries") or {}).get(label, {})
            sample = next((job for job in summary.get("jobs", []) if isinstance(job, dict)), {}) if isinstance(summary, dict) else {}
            sample_result: dict[str, Any] = {}
            if isinstance(sample.get("job_id"), str) and isinstance(sample.get("case_id"), str):
                sample_result_path = root / label / str(sample["case_id"]) / str(sample["job_id"]) / "result.json"
                try:
                    decoded = json.loads(sample_result_path.read_text(encoding="utf-8"))
                    sample_result = decoded if isinstance(decoded, dict) else {}
                except (OSError, json.JSONDecodeError):
                    pass
            protocol_agent = protocol_agents.get(label, {})
            # 新 AgentSpec 的稳定身份优先于版本字符串推断；后者只用于历史 Artifact。
            agent_id = stable_agent_id(agent)
            existing_agent = next((row for row in document["agents"] if row.get("agent_id") == agent_id), {})
            existing_adapter = (existing_agent.get("metadata") or {}).get("adapter_id") if isinstance(existing_agent, dict) else None
            adapter_id = str(sample.get("adapter_id") or sample_result.get("adapter_id") or existing_adapter or "custom")
            version_id = _identifier("ver", {"project": project_id, "agent": agent_id, "version": version})
            version_by_agent_label[label] = version_id
            self._upsert(document, "agents", "agent_id", {
                "agent_id": agent_id, "display_name": agent_id.replace("-", " ").title(), "kind": _kind(adapter_id),
                "created_at": created_at, "metadata": {"adapter_id": adapter_id},
            })
            profile = protocol_agent.get("prompt_profile") if isinstance(protocol_agent.get("prompt_profile"), str) else sample.get("agent_profile") or sample_result.get("agent_profile") or "unrecorded"
            observed_version = {
                "version_id": version_id, "agent_id": agent_id, "version": version, "parent_version_id": None,
                "status": "draft", "change_type": "config",
                "change_summary": "Version observed in Experiment Artifact; lineage was not declared.",
                "created_at": created_at,
                "snapshot": {"adapter_id": adapter_id, "model": str(sample.get("model") or sample_result.get("model") or "unknown"),
                             "prompt_profile": str(profile),
                             "toolset_hash": _hash(sample.get("allowed_tools") or sample_result.get("allowed_tools") or []),
                             "config_hash": _hash({"budget": sample.get("budget") or sample_result.get("budget"), "adapter": adapter_id,
                                                   "protocol_fingerprint": protocol_artifact.get("protocol_fingerprint")})},
            }
            if project_id is not None:
                observed_version["project_id"] = project_id
            existing_version = next((row for row in document["versions"] if row.get("version_id") == version_id), None)
            # Explicit lineage is human-authored governance metadata. Future
            # Artifact indexing may refresh evidence, but must not erase it.
            if isinstance(existing_version, dict) and existing_version.get("lineage_declared") is True:
                observed_version.update({
                    key: existing_version[key]
                    for key in ("parent_version_id", "status", "change_type", "change_summary", "lineage_declared", "lineage_snapshot_overrides")
                    if key in existing_version
                })
                overrides = existing_version.get("lineage_snapshot_overrides")
                if isinstance(overrides, dict):
                    observed_version["snapshot"].update(overrides)
            self._upsert(document, "versions", "version_id", observed_version)

        observed_experiment = {
            "experiment_id": experiment_id, "name": " vs ".join(str(item.get("version")) for item in agents[:2]),
            "baseline_version_id": version_by_agent_label[str(agents[0]["id"])], "candidate_version_id": version_by_agent_label[str(agents[1]["id"])],
            "status": "completed", "created_at": created_at, "completed_at": str(existing_experiment.get("completed_at") or _now()), "case_ids": case_ids,
            "evaluation_context": context, "evaluation_context_hash": evaluation_context_hash(context),
            "evaluator_version": f"metrics-v{report.get('metrics_version', 2)}", "gate_policy_version": "unbound",
            "artifact_root": str(root), "report_path": str(root / "experiment.json"),
            "comparison_basis": comparison_basis, "comparison_basis_hash": evaluation_context_hash(comparison_basis),
            "comparison_summary": _comparison_summary(report),
        }
        if project_id is not None:
            observed_experiment["project_id"] = project_id
        self._upsert(document, "experiments", "experiment_id", observed_experiment)

        for agent in agents[:2]:
            label = str(agent["id"])
            summary = (report.get("summaries") or {}).get(label, {})
            for job in summary.get("jobs", []) if isinstance(summary, dict) else []:
                if not isinstance(job, dict) or not isinstance(job.get("job_id"), str):
                    continue
                trial_id = _identifier("trial", {"experiment": experiment_id, "agent": label, "job": job["job_id"]})
                result_path = root / label / str(job["case_id"]) / str(job["job_id"]) / "result.json"
                try:
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    result = {}
                attempt_rows = []
                attempts_dir = result_path.parent / "attempts"
                if attempts_dir.is_dir():
                    attempt_rows = sorted((path for path in attempts_dir.iterdir() if path.is_dir()), key=lambda path: path.name)
                if not attempt_rows:
                    attempt_rows = [result_path.parent]
                catalog_attempt_ids: list[str] = []
                selected_attempt_id: str | None = None
                selected_source_attempt_id = result.get("attempt_id")
                selected_path = result_path.parent / "selected-attempt.json"
                selected_projection_available = False
                try:
                    selected_payload = json.loads(selected_path.read_text(encoding="utf-8"))
                    selected_source_attempt_id = selected_payload.get("attempt_id")
                    selected_projection_available = isinstance(selected_source_attempt_id, str)
                except (OSError, json.JSONDecodeError):
                    pass
                for index, attempt_dir in enumerate(attempt_rows, start=1):
                    attempt_result_path = attempt_dir / "result.json"
                    try:
                        attempt_result = json.loads(attempt_result_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        attempt_result = result
                    catalog_attempt_id = _identifier("att", {"trial": trial_id, "path": str(attempt_dir)})
                    catalog_attempt_ids.append(catalog_attempt_id)
                    status = _attempt_status(attempt_result.get("status"))
                    self._upsert(document, "attempts", "attempt_id", {
                        "attempt_id": catalog_attempt_id, "trial_id": trial_id, "attempt_index": index, "status": status,
                        "started_at": created_at, "ended_at": created_at, "artifact_dir": str(attempt_dir),
                        "trace_id": attempt_result.get("trace_id") if isinstance(attempt_result.get("trace_id"), str) else None,
                        "source_attempt_id": attempt_dir.name,
                    })
                    if attempt_dir.name == selected_source_attempt_id:
                        selected_attempt_id = catalog_attempt_id
                # Historical Artifacts predate selected-attempt.json. Retain a
                # deterministic read-only fallback only for that legacy data;
                # current evidence must carry an explicit projection.
                if not selected_projection_available and selected_attempt_id is None and catalog_attempt_ids:
                    selected_attempt_id = catalog_attempt_ids[-1]
                trial_status = _attempt_status(result.get("status"))
                self._upsert(document, "trials", "trial_id", {
                    "trial_id": trial_id, "experiment_id": experiment_id, "case_id": str(job["case_id"]),
                    "agent_version_id": version_by_agent_label[label], "trial_index": int(job.get("trial_index", 1)),
                    "status": trial_status, "attempt_ids": catalog_attempt_ids,
                    "selected_attempt_id": selected_attempt_id, "source_job_id": job["job_id"],
                })
        self.save(document)
        return experiment_id

    def index_gate(self, experiment_id: str, gate: dict[str, Any], *, policy_version: str) -> str:
        document = self.load()
        if experiment_id not in {row["experiment_id"] for row in document["experiments"]}:
            raise ValueError("cannot index a gate for an unknown experiment")
        decision = gate.get("decision") if isinstance(gate.get("decision"), dict) else {}
        status = "promote" if decision.get("status") == "promote" else "hold" if decision.get("status") in {"hold", "blocked"} else "inconclusive"
        gate_id = _identifier("gate", {"experiment": experiment_id, "gate": gate})
        self._upsert(document, "gate_decisions", "gate_id", {
            "gate_id": gate_id, "experiment_id": experiment_id, "status": status, "policy_version": policy_version,
            "decided_at": _now(), "rules": list(gate.get("rules") or []),
            "evidence": {"passed": gate.get("passed"), "decision": decision, "diagnostics": gate.get("diagnostics")},
        })
        self.save(document)
        return gate_id

    def history(self, agent_id: str | None = None) -> dict[str, Any]:
        document = self.load()
        versions = document["versions"] if agent_id is None else [row for row in document["versions"] if row["agent_id"] == agent_id]
        version_ids = {row["version_id"] for row in versions}
        experiments = [row for row in document["experiments"] if row["baseline_version_id"] in version_ids or row["candidate_version_id"] in version_ids]
        experiment_ids = {row["experiment_id"] for row in experiments}
        trials = [row for row in document["trials"] if row["experiment_id"] in experiment_ids]
        trial_ids = {row["trial_id"] for row in trials}
        return {"agents": [row for row in document["agents"] if agent_id is None or row["agent_id"] == agent_id], "versions": versions,
                "experiments": experiments, "trials": trials,
                "attempts": [row for row in document["attempts"] if row["trial_id"] in trial_ids],
                "gate_decisions": [row for row in document["gate_decisions"] if row["experiment_id"] in experiment_ids]}

    def timeline(self, experiment_id: str | None = None, *, agent_id: str | None = None) -> dict[str, Any]:
        """Return a lineage-scoped experiment ledger with comparison safety labels."""

        document = self.load()
        experiments = list(document["experiments"])
        current = next((row for row in experiments if row.get("experiment_id") == experiment_id), None)
        if experiment_id is not None and current is None:
            return {"versions": [], "experiments": [], "gate_decisions": [], "current_experiment_id": None}
        if current is not None and agent_id is None:
            current_version_ids = {current["baseline_version_id"], current["candidate_version_id"]}
            lineage_agent_ids = {
                row["agent_id"] for row in document["versions"] if row.get("version_id") in current_version_ids
            }
            lineage_version_ids = {
                row["version_id"] for row in document["versions"] if row.get("agent_id") in lineage_agent_ids
            }
            experiments = [
                row for row in experiments
                if row.get("baseline_version_id") in lineage_version_ids or row.get("candidate_version_id") in lineage_version_ids
            ]
        elif agent_id is not None:
            lineage_version_ids = {
                row["version_id"] for row in document["versions"] if row.get("agent_id") == agent_id
            }
            experiments = [
                row for row in experiments
                if row.get("baseline_version_id") in lineage_version_ids or row.get("candidate_version_id") in lineage_version_ids
            ]
        experiments.sort(key=lambda row: (str(row.get("completed_at") or row.get("created_at") or ""), str(row.get("experiment_id") or "")))
        decorated: list[dict[str, Any]] = []
        previous: dict[str, Any] | None = None
        for row in experiments:
            item = dict(row)
            item["comparability"] = _comparability(previous, row)
            decorated.append(item)
            previous = row
        version_ids = {
            version_id for row in experiments
            for version_id in (row.get("baseline_version_id"), row.get("candidate_version_id"))
            if isinstance(version_id, str)
        }
        experiment_ids = {row["experiment_id"] for row in experiments}
        return {
            "agents": [row for row in document["agents"] if row.get("agent_id") in {
                version.get("agent_id") for version in document["versions"] if version.get("version_id") in version_ids
            }],
            "versions": _lineage_order([row for row in document["versions"] if row.get("version_id") in version_ids]),
            "experiments": decorated,
            "gate_decisions": [row for row in document["gate_decisions"] if row.get("experiment_id") in experiment_ids],
            "current_experiment_id": current.get("experiment_id") if current else (decorated[-1].get("experiment_id") if decorated else None),
        }
