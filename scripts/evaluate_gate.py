#!/usr/bin/env python3
"""Evaluate a candidate promotion policy from an existing experiment report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_lab.gate import evaluate_gate
from regression_lab.evolution_catalog import EvolutionCatalog
from regression_lab.protocol import write_json_atomically


def read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def select_comparison_arm(experiment: dict, arm_id: str | None) -> tuple[dict, str | None]:
    """Adapt one stored multi-arm comparison to the established Gate input."""

    arms = experiment.get("comparison_arms")
    if not isinstance(arms, dict):
        if arm_id:
            raise ValueError("--comparison-id requires an experiment with comparison_arms")
        return experiment, None
    selected_id = arm_id or experiment.get("primary_comparison_id")
    arm = arms.get(selected_id)
    if not isinstance(arm, dict) or not isinstance(arm.get("comparison"), dict):
        raise ValueError(f"unknown comparison arm: {selected_id}")
    baseline_id, candidate_id = arm.get("baseline_id"), arm.get("candidate_id")
    if not isinstance(baseline_id, str) or not isinstance(candidate_id, str):
        raise ValueError(f"invalid comparison arm: {selected_id}")
    selected = dict(experiment)
    selected.update({
        "agents": [item for item in experiment.get("agents", []) if isinstance(item, dict) and item.get("id") in {baseline_id, candidate_id}],
        "baseline_id": baseline_id, "candidate_id": candidate_id,
        "comparison": arm["comparison"],
        "summaries": {label: experiment["summaries"][label] for label in (baseline_id, candidate_id)},
    })
    experiment_ids = experiment.get("evolution_experiment_ids")
    if isinstance(experiment_ids, dict) and isinstance(experiment_ids.get(selected_id), str):
        selected["evolution_experiment_id"] = experiment_ids[selected_id]
    return selected, selected_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", required=True, help="existing experiment.json")
    parser.add_argument("--policy", required=True, help="gate policy JSON")
    parser.add_argument("--output", help="optional gate report JSON path")
    parser.add_argument("--comparison-id", help="candidate arm ID in a multi-arm experiment")
    parser.add_argument("--evolution-catalog", help="Evolution Catalog to index this Gate decision")
    args = parser.parse_args()
    try:
        report_input, selected_arm_id = select_comparison_arm(read_json(Path(args.experiment), "experiment"), args.comparison_id)
        report = evaluate_gate(report_input, read_json(Path(args.policy), "policy"))
    except ValueError as exc:
        print(f"GATE ERROR: {exc}", file=sys.stderr)
        return 2
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if selected_arm_id:
        report["comparison_id"] = selected_arm_id
    if args.output:
        write_json_atomically(Path(args.output), report)
    experiment_id = report_input.get("evolution_experiment_id")
    catalog_value = args.evolution_catalog or report_input.get("evolution_catalog")
    if catalog_value and isinstance(experiment_id, str):
        try:
            EvolutionCatalog(Path(str(catalog_value))).index_gate(
                experiment_id, report, policy_version=Path(args.policy).stem
            )
        except ValueError as exc:
            print(f"EVOLUTION CATALOG ERROR: {exc}", file=sys.stderr)
            return 2
    print(payload)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
