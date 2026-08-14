"""Schema and identity helpers for Agent version evolution history.

This module deliberately validates the catalog contract without persisting
anything.  The current runtime artifacts remain the source of truth for a
Trial; the evolution catalog will index those immutable artifacts in a later
phase.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from regression_lab.manifest import IDENTIFIER_PATTERN


EVOLUTION_SCHEMA_VERSION = 1
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")

AGENT_KINDS = {"external", "react", "replay", "custom"}
VERSION_STATUSES = {"draft", "candidate", "champion", "rejected", "archived"}
CHANGE_TYPES = {"code", "prompt", "model", "tools", "config", "mixed"}
EXPERIMENT_STATUSES = {"planned", "running", "completed", "failed", "archived"}
GATE_STATUSES = {"promote", "hold", "inconclusive"}
ATTEMPT_STATUSES = {
    "queued", "running", "completed", "timed_out", "model_failed",
    "agent_failed", "infra_failed", "trace_incomplete", "cancelled",
}


@dataclass(frozen=True)
class EvolutionValidation:
    """Serializable validation result shared by all catalog entities."""

    valid: bool
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "errors": list(self.errors)}


def canonical_json(value: Any) -> str:
    """Return the stable JSON representation used for content fingerprints."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def evaluation_context_hash(context: dict[str, Any]) -> str:
    """Hash every input that can change whether two experiments are comparable."""

    if not isinstance(context, dict):
        raise TypeError("evaluation context must be an object")
    return "sha256:" + hashlib.sha256(canonical_json(context).encode("utf-8")).hexdigest()


def _required_string(payload: dict[str, Any], field: str, errors: list[str]) -> str | None:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        errors.append(f"{field} must be a non-empty string")
        return None
    return value


def _identifier(payload: dict[str, Any], field: str, errors: list[str]) -> str | None:
    value = _required_string(payload, field, errors)
    if value is not None and not IDENTIFIER_PATTERN.fullmatch(value):
        errors.append(f"{field} must match {IDENTIFIER_PATTERN.pattern!r}")
    return value


def _timestamp(payload: dict[str, Any], field: str, errors: list[str], *, optional: bool = False) -> None:
    value = payload.get(field)
    if value is None and optional:
        return
    if not isinstance(value, str) or not UTC_TIMESTAMP.fullmatch(value):
        errors.append(f"{field} must be an RFC3339 UTC timestamp ending in Z")


def _enum(payload: dict[str, Any], field: str, values: set[str], errors: list[str]) -> str | None:
    value = _required_string(payload, field, errors)
    if value is not None and value not in values:
        errors.append(f"{field} must be one of {sorted(values)}")
    return value


def _object(payload: dict[str, Any], field: str, errors: list[str], *, required: bool = True) -> dict[str, Any] | None:
    value = payload.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return None
    return value


def _string_list(payload: dict[str, Any], field: str, errors: list[str]) -> list[str] | None:
    value = payload.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        errors.append(f"{field} must be a list of non-empty strings")
        return None
    return value


def validate_agent(payload: dict[str, Any]) -> EvolutionValidation:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return EvolutionValidation(False, ("agent must be an object",))
    _identifier(payload, "agent_id", errors)
    _required_string(payload, "display_name", errors)
    _enum(payload, "kind", AGENT_KINDS, errors)
    _timestamp(payload, "created_at", errors)
    metadata = _object(payload, "metadata", errors)
    if metadata is not None and any(not isinstance(key, str) for key in metadata):
        errors.append("metadata keys must be strings")
    return EvolutionValidation(not errors, tuple(errors))


def validate_agent_version(payload: dict[str, Any]) -> EvolutionValidation:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return EvolutionValidation(False, ("agent_version must be an object",))
    _identifier(payload, "version_id", errors)
    _identifier(payload, "agent_id", errors)
    _required_string(payload, "version", errors)
    parent = payload.get("parent_version_id")
    if parent is not None and (not isinstance(parent, str) or not IDENTIFIER_PATTERN.fullmatch(parent)):
        errors.append("parent_version_id must be null or a safe identifier")
    _enum(payload, "status", VERSION_STATUSES, errors)
    _enum(payload, "change_type", CHANGE_TYPES, errors)
    _required_string(payload, "change_summary", errors)
    _timestamp(payload, "created_at", errors)
    snapshot = _object(payload, "snapshot", errors)
    if snapshot is not None:
        for field in ("adapter_id", "model", "prompt_profile", "toolset_hash", "config_hash"):
            _required_string(snapshot, field, errors)
    return EvolutionValidation(not errors, tuple(errors))


def validate_case(payload: dict[str, Any]) -> EvolutionValidation:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return EvolutionValidation(False, ("case must be an object",))
    _identifier(payload, "case_id", errors)
    _identifier(payload, "manifest_id", errors)
    version = payload.get("manifest_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        errors.append("manifest_version must be a positive integer")
    for field in ("fixture_hash", "test_hash", "policy_hash"):
        _required_string(payload, field, errors)
    return EvolutionValidation(not errors, tuple(errors))


def validate_experiment(payload: dict[str, Any]) -> EvolutionValidation:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return EvolutionValidation(False, ("experiment must be an object",))
    _identifier(payload, "experiment_id", errors)
    _required_string(payload, "name", errors)
    _identifier(payload, "baseline_version_id", errors)
    _identifier(payload, "candidate_version_id", errors)
    _enum(payload, "status", EXPERIMENT_STATUSES, errors)
    _timestamp(payload, "created_at", errors)
    _timestamp(payload, "completed_at", errors, optional=True)
    cases = _string_list(payload, "case_ids", errors)
    if cases is not None and len(cases) == 0:
        errors.append("case_ids must not be empty")
    context = _object(payload, "evaluation_context", errors)
    context_hash = _required_string(payload, "evaluation_context_hash", errors)
    if context is not None and context_hash is not None and evaluation_context_hash(context) != context_hash:
        errors.append("evaluation_context_hash does not match evaluation_context")
    _required_string(payload, "evaluator_version", errors)
    _required_string(payload, "gate_policy_version", errors)
    _required_string(payload, "artifact_root", errors)
    return EvolutionValidation(not errors, tuple(errors))


def validate_attempt(payload: dict[str, Any]) -> EvolutionValidation:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return EvolutionValidation(False, ("attempt must be an object",))
    _identifier(payload, "attempt_id", errors)
    _identifier(payload, "trial_id", errors)
    index = payload.get("attempt_index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 1:
        errors.append("attempt_index must be a positive integer")
    _enum(payload, "status", ATTEMPT_STATUSES, errors)
    _timestamp(payload, "started_at", errors)
    _timestamp(payload, "ended_at", errors, optional=True)
    if payload.get("status") in {"completed", "timed_out", "model_failed", "agent_failed", "infra_failed", "trace_incomplete", "cancelled"} and payload.get("ended_at") is None:
        errors.append("ended_at is required for a terminal attempt")
    _required_string(payload, "artifact_dir", errors)
    trace_id = payload.get("trace_id")
    if trace_id is not None and (not isinstance(trace_id, str) or not trace_id):
        errors.append("trace_id must be null or a non-empty string")
    return EvolutionValidation(not errors, tuple(errors))


def validate_gate_decision(payload: dict[str, Any]) -> EvolutionValidation:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return EvolutionValidation(False, ("gate_decision must be an object",))
    _identifier(payload, "gate_id", errors)
    _identifier(payload, "experiment_id", errors)
    _enum(payload, "status", GATE_STATUSES, errors)
    _required_string(payload, "policy_version", errors)
    _timestamp(payload, "decided_at", errors)
    rules = payload.get("rules")
    if not isinstance(rules, list) or not all(isinstance(rule, dict) for rule in rules):
        errors.append("rules must be a list of objects")
    evidence = _object(payload, "evidence", errors)
    if evidence is not None and not evidence:
        errors.append("evidence must not be empty")
    return EvolutionValidation(not errors, tuple(errors))


def validate_trial(payload: dict[str, Any]) -> EvolutionValidation:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return EvolutionValidation(False, ("trial must be an object",))
    _identifier(payload, "trial_id", errors)
    _identifier(payload, "experiment_id", errors)
    _identifier(payload, "case_id", errors)
    _identifier(payload, "agent_version_id", errors)
    index = payload.get("trial_index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 1:
        errors.append("trial_index must be a positive integer")
    _enum(payload, "status", ATTEMPT_STATUSES | {"pending"}, errors)
    attempt_ids = _string_list(payload, "attempt_ids", errors)
    selected = payload.get("selected_attempt_id")
    if selected is not None and (not isinstance(selected, str) or selected not in (attempt_ids or [])):
        errors.append("selected_attempt_id must be null or reference an attempt_ids entry")
    if payload.get("status") == "completed" and not selected:
        errors.append("completed trial requires selected_attempt_id")
    return EvolutionValidation(not errors, tuple(errors))


def validate_evolution_document(payload: dict[str, Any]) -> EvolutionValidation:
    """Validate a complete catalog snapshot and all cross-entity references."""

    errors: list[str] = []
    if not isinstance(payload, dict):
        return EvolutionValidation(False, ("evolution document must be an object",))
    if payload.get("schema_version") != EVOLUTION_SCHEMA_VERSION:
        errors.append(f"schema_version must be {EVOLUTION_SCHEMA_VERSION}")
    collections = {}
    for name, validator in (("agents", validate_agent), ("versions", validate_agent_version),
                            ("cases", validate_case), ("experiments", validate_experiment),
                            ("trials", validate_trial), ("attempts", validate_attempt),
                            ("gate_decisions", validate_gate_decision)):
        value = payload.get(name)
        if not isinstance(value, list):
            errors.append(f"{name} must be a list")
            collections[name] = []
            continue
        collections[name] = value
        seen: set[str] = set()
        id_field = {"agents": "agent_id", "versions": "version_id", "cases": "case_id",
                    "experiments": "experiment_id", "trials": "trial_id", "attempts": "attempt_id",
                    "gate_decisions": "gate_id"}[name]
        for index, item in enumerate(value):
            result = validator(item)
            errors.extend(f"{name}[{index}]: {error}" for error in result.errors)
            if isinstance(item, dict) and isinstance(item.get(id_field), str):
                if item[id_field] in seen:
                    errors.append(f"{name}[{index}]: duplicate {id_field} {item[id_field]}")
                seen.add(item[id_field])

    agent_ids = {item.get("agent_id") for item in collections["agents"] if isinstance(item, dict)}
    version_ids = {item.get("version_id") for item in collections["versions"] if isinstance(item, dict)}
    case_ids = {item.get("case_id") for item in collections["cases"] if isinstance(item, dict)}
    experiment_ids = {item.get("experiment_id") for item in collections["experiments"] if isinstance(item, dict)}
    trial_ids = {item.get("trial_id") for item in collections["trials"] if isinstance(item, dict)}
    attempt_ids = {item.get("attempt_id") for item in collections["attempts"] if isinstance(item, dict)}
    gate_ids = {item.get("gate_id") for item in collections["gate_decisions"] if isinstance(item, dict)}
    parent_by_version = {
        item.get("version_id"): item.get("parent_version_id")
        for item in collections["versions"]
        if isinstance(item, dict) and item.get("version_id")
    }
    for index, item in enumerate(collections["versions"]):
        if isinstance(item, dict):
            if item.get("agent_id") not in agent_ids:
                errors.append(f"versions[{index}]: agent_id does not reference an agent")
            parent = item.get("parent_version_id")
            if parent is not None and parent not in version_ids:
                errors.append(f"versions[{index}]: parent_version_id does not reference a version")
            seen: set[str] = set()
            current = item.get("version_id")
            while current is not None:
                if current in seen:
                    errors.append(f"versions[{index}]: parent_version_id creates a cycle")
                    break
                seen.add(current)
                current = parent_by_version.get(current)
    for index, item in enumerate(collections["experiments"]):
        if isinstance(item, dict):
            for field, values in (("baseline_version_id", version_ids), ("candidate_version_id", version_ids)):
                if item.get(field) not in values:
                    errors.append(f"experiments[{index}]: {field} does not reference a version")
            if any(case not in case_ids for case in item.get("case_ids", [])):
                errors.append(f"experiments[{index}]: case_ids contains an unknown case")
    for index, item in enumerate(collections["trials"]):
        if isinstance(item, dict):
            for field, values in (("experiment_id", experiment_ids), ("case_id", case_ids), ("agent_version_id", version_ids)):
                if item.get(field) not in values:
                    errors.append(f"trials[{index}]: {field} does not reference a known entity")
            if any(attempt not in attempt_ids for attempt in item.get("attempt_ids", [])):
                errors.append(f"trials[{index}]: attempt_ids contains an unknown attempt")
    for index, item in enumerate(collections["attempts"]):
        if isinstance(item, dict) and item.get("trial_id") not in trial_ids:
            errors.append(f"attempts[{index}]: trial_id does not reference a trial")
    for index, item in enumerate(collections["gate_decisions"]):
        if isinstance(item, dict) and item.get("experiment_id") not in experiment_ids:
            errors.append(f"gate_decisions[{index}]: experiment_id does not reference an experiment")
    return EvolutionValidation(not errors, tuple(errors))
