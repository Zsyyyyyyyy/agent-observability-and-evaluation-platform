#!/usr/bin/env python3
"""Evaluate a candidate promotion policy from an existing experiment report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_lab.gate import evaluate_gate


def read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", required=True, help="existing experiment.json")
    parser.add_argument("--policy", required=True, help="gate policy JSON")
    parser.add_argument("--output", help="optional gate report JSON path")
    args = parser.parse_args()
    try:
        report = evaluate_gate(read_json(Path(args.experiment), "experiment"), read_json(Path(args.policy), "policy"))
    except ValueError as exc:
        print(f"GATE ERROR: {exc}", file=sys.stderr)
        return 2
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True); output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
