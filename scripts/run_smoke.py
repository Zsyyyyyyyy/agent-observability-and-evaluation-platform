#!/usr/bin/env python3
"""Create a temporary Git Worktree and run the Day 2 external Agent replay smoke test."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from regression_lab.sandbox import DockerSandbox


REGRESSION = Path(__file__).resolve().parents[1]
FIXTURE = REGRESSION / "fixtures" / "smoke_calculator"
WORKER = REGRESSION / "adapters" / "readonly_replay" / "worker.py"


def run(*args: str, cwd: Path) -> None:
    subprocess.run(list(args), cwd=cwd, check=True, capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-source", required=True, help="path to external agent_entry.py")
    parser.add_argument(
        "--docker",
        action="store_true",
        help="run test command inside Docker Sandbox instead of host replay mode",
    )
    parser.add_argument(
        "--bash",
        action="store_true",
        help="include a replayed bash tool call (requires --docker)",
    )
    args = parser.parse_args()
    replay_source = Path(args.replay_source).expanduser().resolve()
    if not replay_source.is_file():
        print(f"external Agent source does not exist: {replay_source}", file=sys.stderr)
        return 2
    if args.bash and not args.docker:
        print("--bash requires --docker", file=sys.stderr)
        return 2
    if args.docker:
        available, detail = DockerSandbox.available()
        if not available:
            print(f"DOCKER UNAVAILABLE: {detail}", file=sys.stderr)
            return 2

    runtime = REGRESSION / ".runtime" / "smoke"
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.mkdir(parents=True)
    base_repo = runtime / "base-repo"
    worktree = runtime / "worktree"
    shutil.copytree(FIXTURE, base_repo)
    run("git", "init", cwd=base_repo)
    run("git", "config", "user.email", "regression-lab@example.invalid", cwd=base_repo)
    run("git", "config", "user.name", "Regression Lab", cwd=base_repo)
    run("git", "add", ".", cwd=base_repo)
    run("git", "commit", "-m", "fixture baseline", cwd=base_repo)
    run("git", "worktree", "add", "-b", "smoke-run", str(worktree), "HEAD", cwd=base_repo)

    input_path = runtime / "trial-input.json"
    result_path = runtime / "result.json"
    trace_path = runtime / "trace.jsonl"
    store_path = runtime / "runs.db"
    input_path.write_text(json.dumps({
        "trial_id": "trial_smoke_001",
        "agent_version": "readonly-replay-v1",
        "case_id": "smoke_calculator_empty_input",
        "prompt": "请修复计算器在收到空输入时抛出异常的问题，并运行测试。",
        "worktree": str(worktree),
        "replay_source": str(replay_source),
        "test_command": "python -m unittest discover -s tests -v" if args.docker else "python3.11 -m unittest discover -s tests -v",
        "sandbox": {"image": "python:3.11-slim"} if args.docker else None,
        "replay_bash": args.bash,
        "allowed_paths": ["src/**"],
        "forbidden_paths": ["tests/**"],
        "trace_output": str(trace_path),
        "result_output": str(result_path),
        "run_store": str(store_path),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    env = {**os.environ, "PYTHONPATH": str(REGRESSION / "src")}
    completed = subprocess.run(
        [sys.executable, str(WORKER), "--input", str(input_path)],
        cwd=REGRESSION,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    print(completed.stdout)
    if completed.stderr:
        print(completed.stderr, file=sys.stderr)
    if completed.returncode != 0:
        return completed.returncode

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed", result
    assert result["test_exit_code"] == 0, result
    assert "src/calculator.py" in result["changed_files"], result
    summary = result["trace_summary"]
    assert summary["status"] == "complete", summary
    assert result["trace_validation"]["valid"] is True, result
    assert result["evaluation"]["passed"] is True, result
    assert {score["evaluator"] for score in result["scores"]} == {
        "test", "path_policy", "trace_completeness", "diff", "tool_integrity", "budget"
    }, result
    assert store_path.exists(), store_path
    assert "model.call" in summary["names"], summary
    assert "tool.call" in summary["names"], summary
    print("SMOKE PASS")
    print(json.dumps({
        "status": result["status"],
        "changed_files": result["changed_files"],
        "test_exit_code": result["test_exit_code"],
        "trace_summary": summary,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
