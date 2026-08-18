#!/usr/bin/env python3
"""Run deterministic, Manifest-driven failure probes against the real Sandbox."""

from __future__ import annotations

import argparse
import hashlib
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


def probe_command(manifest: str, case_dir: Path) -> list[str]:
    return [
        sys.executable, str(ROOT / "scripts" / "run_benchmark.py"),
        "--manifest", str(ROOT / "benchmarks" / manifest), "--output-dir", str(case_dir),
        "--adapter", "failure-probe", "--docker", "--resume",
    ]


def relocated_case_dir(output: Path, manifest: str, retry: int = 0) -> Path:
    """Allocate a separate namespace after a repository relocation.

    A Trial fingerprint intentionally includes its resolved fixture path.  An
    Artifact from a moved checkout is therefore not owned by the new checkout
    and must never be overwritten.  This namespace preserves it while making
    the self-test runnable in the current repository.
    """

    workspace_id = hashlib.sha256(str(ROOT.resolve()).encode("utf-8")).hexdigest()[:12]
    namespace = f"workspace-{workspace_id}" if retry == 0 else f"workspace-{workspace_id}-{retry + 1}"
    return output / namespace / manifest.removesuffix(".yaml")


def probe_passed(
    returncode: int,
    result: dict[str, object],
    expected_status: str,
    expected_failed_score: str,
) -> bool:
    """Accept both a fresh expected failure and a validated resumed Artifact.

    ``run_benchmark`` returns 1 when it executes an expected failing Trial,
    but returns 0 when ``--resume`` reuses that same already-validated result.
    The persisted evidence remains the authority in both cases.
    """

    scores = {
        score.get("evaluator"): score
        for score in result.get("scores", [])
        if isinstance(score, dict)
    }
    return (
        returncode in {0, 1}
        and result.get("status") == expected_status
        and (result.get("trace_validation") or {}).get("valid") is True
        and (scores.get(expected_failed_score) or {}).get("passed") is False
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(ROOT / ".runtime" / "failure-suite"))
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    rows = []
    for manifest, (expected_status, expected_failed_score) in EXPECTATIONS.items():
        case_dir = output / manifest.removesuffix(".yaml")
        completed = subprocess.run(probe_command(manifest, case_dir), cwd=ROOT, capture_output=True, text=True, check=False)
        retry = 0
        while completed.returncode == 2 and "REFUSING UNOWNED OUTPUT DIRECTORY" in completed.stderr and retry < 100:
            case_dir = relocated_case_dir(output, manifest, retry)
            completed = subprocess.run(probe_command(manifest, case_dir), cwd=ROOT, capture_output=True, text=True, check=False)
            retry += 1
        result_path = next(case_dir.rglob("result.json"), None)
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path else {}
        passed = probe_passed(completed.returncode, result, expected_status, expected_failed_score)
        rows.append({"manifest": manifest, "passed": passed, "status": result.get("status"), "failed_score": expected_failed_score, "artifact_root": str(case_dir)})
    report = {"passed": all(row["passed"] for row in rows), "probes": rows}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
