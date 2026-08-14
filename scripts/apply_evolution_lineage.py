#!/usr/bin/env python3
"""Apply an explicit, reviewable version lineage to an Evolution Catalog.

The declaration documents human-owned version semantics.  Experiment Artifacts
remain immutable; this script updates only the rebuildable local Catalog index.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_lab.evolution_catalog import EvolutionCatalog


VALID_STATUSES = {"draft", "candidate", "champion", "rejected", "archived"}
VALID_CHANGE_TYPES = {"code", "prompt", "model", "tools", "config", "mixed"}


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def apply_lineage(catalog: EvolutionCatalog, declaration: dict[str, Any]) -> dict[str, Any]:
    if declaration.get("schema_version") != 1:
        raise ValueError("lineage declaration requires schema_version 1")
    agent_id = declaration.get("agent_id")
    display_name = declaration.get("display_name")
    versions = declaration.get("versions")
    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError("lineage declaration requires agent_id")
    if not isinstance(display_name, str) or not display_name:
        raise ValueError("lineage declaration requires display_name")
    if not isinstance(versions, list) or not versions:
        raise ValueError("lineage declaration requires versions")

    document = catalog.load()
    agent = next((row for row in document["agents"] if row.get("agent_id") == agent_id), None)
    if not isinstance(agent, dict):
        raise ValueError(f"agent {agent_id!r} is not indexed; index its Artifacts first")
    agent["display_name"] = display_name
    indexed = {
        row.get("version"): row
        for row in document["versions"]
        if isinstance(row, dict) and row.get("agent_id") == agent_id and isinstance(row.get("version"), str)
    }
    declared_names = []
    for item in versions:
        if not isinstance(item, dict):
            raise ValueError("lineage version entries must be objects")
        name = item.get("version")
        parent_name = item.get("parent_version")
        status = item.get("status")
        change_type = item.get("change_type")
        summary = item.get("change_summary")
        profile = item.get("prompt_profile")
        if not isinstance(name, str) or name not in indexed:
            raise ValueError(f"lineage version {name!r} is not indexed")
        if parent_name is not None and (not isinstance(parent_name, str) or parent_name not in indexed):
            raise ValueError(f"lineage parent {parent_name!r} is not indexed")
        if status not in VALID_STATUSES:
            raise ValueError(f"lineage version {name!r} has invalid status")
        if change_type not in VALID_CHANGE_TYPES:
            raise ValueError(f"lineage version {name!r} has invalid change_type")
        if not isinstance(summary, str) or not summary:
            raise ValueError(f"lineage version {name!r} requires change_summary")
        if profile is not None and (not isinstance(profile, str) or not profile):
            raise ValueError(f"lineage version {name!r} has invalid prompt_profile")
        declared_names.append(name)

    if len(set(declared_names)) != len(declared_names):
        raise ValueError("lineage declaration repeats a version")
    for item in versions:
        row = indexed[item["version"]]
        parent_name = item["parent_version"]
        row.update({
            "parent_version_id": indexed[parent_name]["version_id"] if parent_name else None,
            "status": item["status"],
            "change_type": item["change_type"],
            "change_summary": item["change_summary"],
            "lineage_declared": True,
        })
        if "prompt_profile" in item:
            snapshot = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else {}
            snapshot["prompt_profile"] = item["prompt_profile"]
            row["snapshot"] = snapshot
            row["lineage_snapshot_overrides"] = {"prompt_profile": item["prompt_profile"]}
    catalog.save(document)
    return {"agent_id": agent_id, "display_name": display_name, "declared_versions": declared_names}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, help="local evolution-catalog.json")
    parser.add_argument("--declaration", required=True, help="explicit lineage JSON")
    args = parser.parse_args()
    try:
        result = apply_lineage(
            EvolutionCatalog(Path(args.catalog)),
            read_json(Path(args.declaration), "lineage declaration"),
        )
    except ValueError as exc:
        print(f"LINEAGE ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
