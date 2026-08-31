"""Benchmark Case Manifest loading, validation, and Trial task expansion."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ManifestError(ValueError):
    pass


IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SUPPORTED_EVALUATORS = frozenset({"test", "path_policy", "diff", "tool_integrity", "budget", "trace_completeness"})
SUPPORTED_ACCEPTANCE = frozenset({
    "test_exit_code == 0", "forbidden_path_changes == 0", "trace_status == complete", "result_status == completed",
    "path_policy blocks", "tool_integrity blocks", "timeout blocks",
})


def validate_identifier(value: Any, field: str) -> str:
    """Accept a single safe path component used in runtime output paths."""

    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise ManifestError(
            f"{field} must match {IDENTIFIER_PATTERN.pattern!r}; path separators and traversal are forbidden"
        )
    return value


def safe_child_path(parent: str | Path, identifier: Any, field: str) -> Path:
    """Return a resolved child path, rejecting traversal and symlink escapes."""

    safe_name = validate_identifier(identifier, field)
    root = Path(parent).resolve()
    candidate = (root / safe_name).resolve()
    if candidate.parent != root:
        raise ManifestError(f"{field} escapes its output directory")
    return candidate


def project_path(project_root: str | Path, relative_path: Any, field: str) -> Path:
    """Resolve a configured project-relative path without allowing root escape."""

    if not isinstance(relative_path, str) or not relative_path or Path(relative_path).is_absolute():
        raise ManifestError(f"{field} must be a non-empty relative path")
    root = Path(project_root).resolve()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root):
        raise ManifestError(f"{field} escapes project_root")
    return candidate


def _scalar(value: str) -> Any:
    value = value.strip()
    if value in {"", "null", "~"}:
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    if (value.startswith("\"") and value.endswith("\"")) or (value.startswith("'") and value.endswith("'")):
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            items = value[1:-1].strip()
            if not items:
                return []
            return [_scalar(item.strip()) for item in items.split(",")]
    if value.startswith("{") and value.endswith("}"):
        items = value[1:-1].strip()
        if not items:
            return {}
        result: dict[str, Any] = {}
        for item in items.split(","):
            key, separator, item_value = item.partition(":")
            if not separator or not key.strip():
                return value
            result[key.strip()] = _scalar(item_value.strip())
        return result
    return value


def _simple_yaml(text: str) -> dict[str, Any]:
    """Parse the small YAML subset used by local benchmark manifests.

    This intentionally supports maps, scalar lists, and folded (`>-`) strings;
    a full YAML dependency is not required for the Regression Lab MVP.
    """

    raw: list[tuple[int, str]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        raw.append((indent, line.strip()))

    def parse(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(raw) or raw[index][0] < indent:
            return {}, index
        is_list = raw[index][0] == indent and raw[index][1].startswith("-")
        result: Any = [] if is_list else {}
        while index < len(raw) and raw[index][0] == indent:
            content = raw[index][1]
            if is_list:
                if not content.startswith("-"):
                    break
                item = content[1:].strip()
                result.append(_scalar(item))
                index += 1
                continue
            if ":" not in content:
                raise ManifestError(f"invalid YAML line: {content}")
            key, value = content.split(":", 1)
            key = key.strip()
            value = value.strip()
            index += 1
            if value in {">-", ">"}:
                folded: list[str] = []
                while index < len(raw) and raw[index][0] > indent:
                    folded.append(raw[index][1])
                    index += 1
                result[key] = " ".join(folded)
            elif value in {"|-", "|"}:
                block: list[str] = []
                while index < len(raw) and raw[index][0] > indent:
                    block.append(raw[index][1])
                    index += 1
                result[key] = "\n".join(block)
            elif value == "":
                if index < len(raw) and raw[index][0] > indent:
                    result[key], index = parse(index, raw[index][0])
                else:
                    result[key] = None
            else:
                result[key] = _scalar(value)
        return result, index

    parsed, position = parse(0, raw[0][0] if raw else 0)
    if position != len(raw) or not isinstance(parsed, dict):
        raise ManifestError("manifest must contain a top-level map")
    return parsed


def load_mapping_document(path: str | Path, *, document_name: str) -> dict[str, Any]:
    """Load the small YAML/JSON mapping format shared by local configs."""

    document_path = Path(path).resolve()
    text = document_path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError:
            parsed = _simple_yaml(text)
        else:
            parsed = yaml.safe_load(text)
    if not isinstance(parsed, dict):
        raise ManifestError(f"{document_name} root must be a map")
    return parsed


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    parsed = load_mapping_document(manifest_path, document_name="manifest")
    parsed["_manifest_path"] = str(manifest_path)
    return parsed


@dataclass(frozen=True)
class ManifestValidation:
    valid: bool
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "errors": list(self.errors)}


def validate_manifest(manifest: dict[str, Any], project_root: str | Path | None = None) -> ManifestValidation:
    errors: list[str] = []

    def required(path: str, value: Any) -> None:
        if value is None or value == "":
            errors.append(f"{path} is required")

    for field in ("schema_version", "id", "version", "title", "fixture", "task", "execution", "tool_policy", "evaluators", "acceptance"):
        required(field, manifest.get(field))
    try:
        validate_identifier(manifest.get("id"), "id")
    except ManifestError as exc:
        errors.append(str(exc))
    if not isinstance(manifest.get("schema_version"), int):
        errors.append("schema_version must be an integer")
    if not isinstance(manifest.get("version"), int):
        errors.append("version must be an integer")

    fixture = manifest.get("fixture")
    task = manifest.get("task")
    execution = manifest.get("execution")
    policy = manifest.get("tool_policy")
    if not isinstance(fixture, dict):
        errors.append("fixture must be a map")
    else:
        required("fixture.path", fixture.get("path"))
        required("fixture.test_command", fixture.get("test_command"))
        if fixture.get("path") and project_root:
            try:
                fixture_path = project_path(project_root, fixture["path"], "fixture.path")
            except ManifestError as exc:
                errors.append(str(exc))
            else:
                if not fixture_path.is_dir():
                    errors.append(f"fixture.path does not exist: {fixture_path}")
    if not isinstance(task, dict):
        errors.append("task must be a map")
    else:
        required("task.prompt", task.get("prompt"))
        for field in ("allowed_paths", "forbidden_paths"):
            if not isinstance(task.get(field), list) or not all(isinstance(item, str) for item in task.get(field, [])):
                errors.append(f"task.{field} must be a string list")
    if not isinstance(execution, dict):
        errors.append("execution must be a map")
    else:
        for field in ("timeout_seconds", "max_tokens", "max_tool_calls", "trials"):
            if not isinstance(execution.get(field), int) or execution[field] <= 0:
                errors.append(f"execution.{field} must be a positive integer")
        if execution.get("network") not in {"none", "host", "bridge"}:
            errors.append("execution.network must be none, host, or bridge")
    if not isinstance(policy, dict):
        errors.append("tool_policy must be a map")
    else:
        for field in ("allow", "deny"):
            if not isinstance(policy.get(field), list) or not all(isinstance(item, str) for item in policy.get(field, [])):
                errors.append(f"tool_policy.{field} must be a string list")
    evaluators = manifest.get("evaluators")
    if not isinstance(evaluators, dict) or not isinstance(evaluators.get("required"), list):
        errors.append("evaluators.required must be a list")
    elif not evaluators["required"] or not all(
        isinstance(item, str) and item in SUPPORTED_EVALUATORS for item in evaluators["required"]
    ) or len(evaluators["required"]) != len(set(evaluators["required"])):
        errors.append("evaluators.required must be a non-empty unique list of supported evaluators")
    acceptance = manifest.get("acceptance")
    if not isinstance(acceptance, dict) or not isinstance(acceptance.get("must"), list):
        errors.append("acceptance.must must be a list")
    elif not acceptance["must"] or not all(
        isinstance(item, str) and item in SUPPORTED_ACCEPTANCE for item in acceptance["must"]
    ) or len(acceptance["must"]) != len(set(acceptance["must"])):
        errors.append("acceptance.must must be a non-empty unique list of supported acceptance checks")
    failure_mode = manifest.get("failure_mode")
    if failure_mode is not None and failure_mode not in {"path_violation", "unauthorized_tool", "timeout"}:
        errors.append("failure_mode must be path_violation, unauthorized_tool, or timeout")
    return ManifestValidation(not errors, tuple(errors))


def expand_trials(manifest: dict[str, Any], project_root: str | Path | None = None,
                  trials_override: int | None = None) -> list[dict[str, Any]]:
    validation = validate_manifest(manifest, project_root)
    if not validation.valid:
        raise ManifestError("invalid manifest: " + "; ".join(validation.errors))
    fixture = manifest["fixture"]
    task = manifest["task"]
    execution = manifest["execution"]
    count = trials_override or execution["trials"]
    root = Path(project_root or Path(manifest["_manifest_path"]).resolve().parents[1]).resolve()
    fixture_path = project_path(root, fixture["path"], "fixture.path")
    jobs = []
    for trial_index in range(1, count + 1):
        jobs.append({
            "job_id": f"{manifest['id']}_trial_{trial_index:03d}",
            "case_id": manifest["id"],
            "case_version": manifest["version"],
            "trial_index": trial_index,
            "title": manifest["title"],
            "fixture_path": str(fixture_path),
            "prompt": task["prompt"],
            "test_command": fixture["test_command"],
            "allowed_paths": task["allowed_paths"],
            "forbidden_paths": task["forbidden_paths"],
            "budget": {
                "max_tool_calls": execution["max_tool_calls"],
                "max_duration_ms": execution["timeout_seconds"] * 1000,
            },
            "max_tokens": execution["max_tokens"],
            "max_retries": execution.get("max_retries", 0),
            "sandbox": {
                "network": execution["network"],
                "timeout_seconds": execution["timeout_seconds"],
            },
            "tool_policy": manifest["tool_policy"],
            "required_evaluators": manifest["evaluators"]["required"],
            "acceptance_must": manifest["acceptance"]["must"],
            "failure_mode": manifest.get("failure_mode"),
        })
    return jobs
