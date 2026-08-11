#!/usr/bin/env python3
"""Evaluate an existing Trial Result with deterministic baseline evaluators."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_lab.evaluators import evaluate_baseline
from regression_lab.store import RunStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True, help="path to a Worker result.json")
    parser.add_argument("--persist", action="store_true", help="rewrite result and update its Run Store")
    parser.add_argument("--store", help="override the SQLite Run Store path when persisting")
    args = parser.parse_args()

    result_path = Path(args.result).resolve()
    result = json.loads(result_path.read_text(encoding="utf-8"))
    evaluation = evaluate_baseline(result)
    result["evaluation"] = evaluation
    result["scores"] = evaluation["scores"]

    if args.persist:
        store_path = args.store or result.get("run_store")
        if not store_path:
            parser.error("--persist requires --store or result.run_store")
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        store = RunStore(store_path)
        store.record_run(result, result["scores"])

    print(json.dumps(evaluation, ensure_ascii=False, indent=2))
    return 0 if evaluation["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
