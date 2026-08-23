#!/usr/bin/env python3
"""Export a small, portable, read-only Console demo from an Experiment Runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


ABSOLUTE_PATH = re.compile(r"/(?:Users|home|private|var|tmp)/[^\s\"']+")
SECRET_VALUE = re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{12,}\b")
REPORT_FILES = ("experiment.json", "protocol.json", "execution-plan.json", "gate-report.json", "gate-negative.json")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON artifact: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be a map: {path.name}")
    return value


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return SECRET_VALUE.sub("[REDACTED]", ABSOLUTE_PATH.sub("<local-path>", value))
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_redacted_trace(source: Path, destination: Path) -> None:
    """Trace 可能包含工具输出，必须和报告 JSON 使用相同的脱敏规则。"""

    events: list[str] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid Trace JSONL at line {line_number}") from exc
        events.append(json.dumps(_redact(event), ensure_ascii=False, sort_keys=True))
    destination.write_text("\n".join(events) + "\n", encoding="utf-8")


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def export_demo(source: str | Path, output: str | Path, *, catalog: str | Path | None = None) -> dict[str, Any]:
    source_root = Path(source).resolve()
    output_root = Path(output).resolve()
    if not source_root.is_dir():
        raise ValueError("source runtime does not exist")
    if output_root.exists():
        raise ValueError("output directory already exists")
    experiment_path = source_root / "experiment.json"
    if not experiment_path.is_file():
        raise ValueError("source runtime is missing experiment.json")
    selected_results = [
        path for path in source_root.rglob("result.json")
        if "attempts" not in path.parts and "invalid-attempts" not in path.parts
    ]
    if not selected_results:
        raise ValueError("source runtime has no selected Trial results")

    output_root.mkdir(parents=True)
    for filename in REPORT_FILES:
        path = source_root / filename
        if path.is_file():
            report = _redact(_read_json(path))
            if filename == "experiment.json" and catalog is not None:
                report["evolution_catalog"] = "evolution-catalog.json"
            _write_json(output_root / filename, report)

    for result_path in selected_results:
        trial_root = result_path.parent
        target_root = output_root / trial_root.relative_to(source_root)
        raw_result = _read_json(result_path)
        trace_path = Path(str(raw_result.get("trace_path", "")))
        if not trace_path.is_absolute():
            trace_path = trial_root / trace_path
        result = _redact(raw_result)
        target_root.mkdir(parents=True, exist_ok=True)
        if trace_path.is_file():
            _write_redacted_trace(trace_path, target_root / "trace.jsonl")
            result["trace_path"] = "trace.jsonl"
        else:
            result["trace_path"] = ""
        _write_json(target_root / "result.json", result)

    if catalog is not None:
        catalog_path = Path(catalog).resolve()
        if not catalog_path.is_file():
            raise ValueError("catalog file does not exist")
        _write_json(output_root / "evolution-catalog.json", _redact(_read_json(catalog_path)))

    files = [
        {"path": str(path.relative_to(output_root)), "sha256": _digest(path)}
        for path in sorted(output_root.rglob("*")) if path.is_file()
    ]
    manifest = {
        "schema_version": 1,
        "kind": "regression-lab-readonly-demo",
        "trial_count": len(selected_results),
        "files": files,
        "note": "Read-only demo export. Source paths, local secrets, Attempts and Worktrees are excluded.",
    }
    _write_json(output_root / "demo-manifest.json", manifest)
    return manifest


def verify_demo(output: str | Path) -> dict[str, Any]:
    """校验离线 Demo 的文件集合和内容摘要，不执行任何 Agent。"""

    root = Path(output).resolve()
    errors: list[str] = []
    try:
        manifest = _read_json(root / "demo-manifest.json")
    except ValueError as exc:
        return {"valid": False, "file_count": 0, "errors": [str(exc)]}
    entries = manifest.get("files")
    if manifest.get("kind") != "regression-lab-readonly-demo" or not isinstance(entries, list):
        return {"valid": False, "file_count": 0, "errors": ["invalid demo manifest"]}

    expected_paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            errors.append("manifest contains an invalid file entry")
            continue
        relative_path = entry["path"]
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(root):
            errors.append(f"file escapes demo root: {relative_path}")
            continue
        expected_paths.add(relative_path)
        if not candidate.is_file():
            errors.append(f"file is missing: {relative_path}")
        elif entry.get("sha256") != _digest(candidate):
            errors.append(f"digest mismatch: {relative_path}")

    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "demo-manifest.json"
    }
    for relative_path in sorted(actual_paths - expected_paths):
        errors.append(f"untracked demo file: {relative_path}")
    return {"valid": not errors, "file_count": len(expected_paths), "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export or verify a portable, read-only Console demo")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", help="existing Experiment Runtime directory")
    source.add_argument("--verify", help="existing exported Demo directory")
    parser.add_argument("--output", help="new empty destination directory")
    parser.add_argument("--catalog", help="optional Evolution Catalog JSON to include")
    parser.add_argument("--quiet", action="store_true", help="suppress successful output")
    args = parser.parse_args()
    if args.verify:
        report = verify_demo(args.verify)
        if not args.quiet or not report["valid"]:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["valid"] else 1
    if not args.output:
        parser.error("--output is required with --source")
    try:
        manifest = export_demo(args.source, args.output, catalog=args.catalog)
    except ValueError as exc:
        print(f"Demo export failed: {exc}", file=sys.stderr)
        return 2
    if not args.quiet:
        print(f"Exported {manifest['trial_count']} selected Trials to {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
