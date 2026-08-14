#!/usr/bin/env python3
"""Produce controlled invalid Trials without contacting a model provider."""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from regression_lab.evaluators import evaluate_baseline
from regression_lab.sandbox import DockerSandbox, SandboxConfig
from regression_lab.schema import validate_trace
from regression_lab.store import RunStore
from regression_lab.artifacts import write_json_atomically
from regression_lab.trace import TraceCollector


def _git(worktree: Path, *args: str) -> str:
    import subprocess
    completed = subprocess.run(["git", "-C", str(worktree), *args], text=True, capture_output=True, check=False)
    if completed.returncode:
        raise RuntimeError((completed.stdout + completed.stderr).strip())
    return completed.stdout


def _evidence(result: dict[str, Any], worktree: Path) -> None:
    _git(worktree, "add", "-N", "--", ".")
    status = _git(worktree, "status", "--porcelain")
    result["changed_files"] = [line[3:] for line in status.splitlines() if line.strip()]
    result["git_diff"] = _git(worktree, "diff", "HEAD", "--no-ext-diff", "--binary")
    result["git_evidence"] = {"diff_base": "HEAD", "captures_untracked": True, "status_porcelain": status}


def _test(worktree: Path, spec: dict[str, Any], sandbox: DockerSandbox | None) -> dict[str, Any]:
    if sandbox:
        value = sandbox.run(str(spec["test_command"]), timeout_seconds=sandbox.config.timeout_seconds)
        return {"exit_code": value.exit_code, "stdout": value.stdout, "stderr": value.stderr, "duration_ms": value.duration_ms}
    import subprocess
    value = subprocess.run(str(spec["test_command"]).split(), cwd=worktree, text=True, capture_output=True, check=False)
    return {"exit_code": value.returncode, "stdout": value.stdout, "stderr": value.stderr, "duration_ms": 0}


def run_trial(spec: dict[str, Any]) -> dict[str, Any]:
    mode = spec.get("failure_mode")
    if mode not in {"path_violation", "unauthorized_tool", "timeout"}:
        raise ValueError("failure-probe requires a supported failure_mode")
    trial_id, worktree = str(spec["trial_id"]), Path(spec["worktree"]).resolve()
    trace = TraceCollector(spec["trace_output"], f"trace_{uuid.uuid4().hex[:12]}")
    root = trace.start_span("agent.run", trial_id=trial_id, adapter_id="failure-probe", failure_mode=mode)
    started = time.monotonic()
    result: dict[str, Any] = {
        "trial_id": trial_id, "adapter_id": "failure-probe", "adapter_version": "failure-probe-v1",
        "agent_version": spec.get("agent_version"), "attempt_id": spec.get("attempt_id"), "status": "infra_failed", "trace_id": trace.trace_id,
        "agent_response": "controlled failure probe", "changed_files": [], "test_exit_code": -1,
        "allowed_paths": spec.get("allowed_paths", ["**"]), "forbidden_paths": spec.get("forbidden_paths", []),
        "allowed_tools": spec.get("allowed_tools", []), "denied_tools": spec.get("denied_tools", []),
        "budget": spec.get("budget", {}), "trace_path": str(spec["trace_output"]), "model_usage": {}, "error": None,
    }
    sandbox_spec = spec.get("sandbox")
    sandbox = DockerSandbox(worktree, SandboxConfig(**sandbox_spec)) if sandbox_spec else None
    try:
        if mode == "timeout":
            result["status"] = "timed_out"
            result["error"] = "controlled timeout probe"
        else:
            name = "edit_file" if mode == "path_violation" else "connect_mcp"
            tool = trace.start_span("tool.call", parent_id=root, tool_name=name, probe_mode=mode)
            if mode == "path_violation":
                (worktree / "src" / "calculator.py").write_text('def calculate(value):\n    return 0 if value == "" else int(value) + 1\n', encoding="utf-8")
                (worktree / "tests" / "probe_marker.py").write_text("# intentional policy violation\n", encoding="utf-8")
            else:
                (worktree / "src" / "calculator.py").write_text('def calculate(value):\n    return 0 if value == "" else int(value) + 1\n', encoding="utf-8")
            trace.end_span(tool, status="ok", duration_ms=1.0)
            test = _test(worktree, spec, sandbox)
            result.update({"test_exit_code": test["exit_code"], "test_stdout": test["stdout"], "test_stderr": test["stderr"], "test_duration_ms": test["duration_ms"]})
            result["status"] = "completed" if test["exit_code"] == 0 else "agent_failed"
    except Exception as exc:
        result.update({"status": "infra_failed", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        _evidence(result, worktree)
        trace.end_span(root, status=result["status"], duration_ms=round((time.monotonic() - started) * 1000, 3))
        result["trace_validation"] = validate_trace(result["trace_path"], expected_trace_id=trace.trace_id, expected_trial_id=trial_id).as_dict()
        result["trace_summary"] = trace.summary()
        result["evaluation"] = evaluate_baseline(result)
        result["scores"] = result["evaluation"]["scores"]
        if spec.get("run_store"):
            RunStore(spec["run_store"]).record_run(result, result["scores"])
        write_json_atomically(spec["result_output"], result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--input", required=True); args = parser.parse_args()
    result = run_trial(json.loads(Path(args.input).read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
