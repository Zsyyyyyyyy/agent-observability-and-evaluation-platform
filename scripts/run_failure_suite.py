#!/usr/bin/env python3
"""Run deterministic, Manifest-driven failure probes against the real Sandbox."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTATIONS = {
    "failure-path-violation.yaml": ("completed", "path_policy"),
    "failure-unauthorized-tool.yaml": ("completed", "tool_integrity"),
    "failure-timeout.yaml": ("timed_out", "test"),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(ROOT / ".runtime" / "failure-suite"))
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    rows = []
    for manifest, (expected_status, expected_failed_score) in EXPECTATIONS.items():
        case_dir = output / manifest.removesuffix(".yaml")
        command = [
            sys.executable, str(ROOT / "scripts" / "run_benchmark.py"),
            "--manifest", str(ROOT / "benchmarks" / manifest), "--output-dir", str(case_dir),
            "--adapter", "failure-probe", "--docker", "--resume",
        ]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        result_path = next(case_dir.rglob("result.json"), None)
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path else {}
        scores = {score.get("evaluator"): score for score in result.get("scores", []) if isinstance(score, dict)}
        passed = (
            completed.returncode == 1 and result.get("status") == expected_status
            and (result.get("trace_validation") or {}).get("valid") is True
            and (scores.get(expected_failed_score) or {}).get("passed") is False
        )
        rows.append({"manifest": manifest, "passed": passed, "status": result.get("status"), "failed_score": expected_failed_score})
    report = {"passed": all(row["passed"] for row in rows), "probes": rows}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
