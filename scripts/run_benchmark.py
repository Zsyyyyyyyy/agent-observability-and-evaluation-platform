#!/usr/bin/env python3
"""Expand and execute a deterministic Benchmark Case Manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_lab.manifest import (
    ManifestError,
    expand_trials,
    load_manifest,
    safe_child_path,
    validate_manifest,
)
from regression_lab.adapters import AdapterError, get_adapter
from regression_lab.runner import run_with_deadline
from regression_lab.sandbox import DockerSandbox


REGRESSION = Path(__file__).resolve().parents[1]


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _job_fingerprint(
    job: dict[str, object], *, adapter_id: str, agent_version: str, use_docker: bool, replay_bash: bool,
) -> str:
    payload = {
        "job": job,
        "adapter_id": adapter_id,
        "agent_version": agent_version,
        "execution_mode": "docker" if use_docker else "unsafe_trusted_host",
        "replay_bash": replay_bash,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _owned_job_dir(job_dir: Path, job_id: str, fingerprint: str) -> bool:
    marker = job_dir / "run-manifest.json"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload == {"schema_version": 2, "job_id": job_id, "fingerprint": fingerprint}


def _create_job_dir(job_dir: Path, job_id: str, fingerprint: str) -> None:
    job_dir.mkdir(parents=True)
    (job_dir / "run-manifest.json").write_text(
        json.dumps({"schema_version": 2, "job_id": job_id, "fingerprint": fingerprint}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _timed_out_result(job: dict[str, object], timeout_seconds: int) -> dict[str, object]:
    """Persist a result even when the worker itself misses the trial deadline."""

    return {
        "trial_id": job["job_id"],
        "status": "timed_out",
        "trace_id": None,
        "error": f"parent runner deadline exceeded ({timeout_seconds}s)",
        "test_exit_code": -1,
        "scores": [],
        "evaluation": {"passed": False, "reason": "worker deadline exceeded"},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", default=str(REGRESSION / ".runtime" / "benchmark"))
    parser.add_argument("--project-root", default=str(REGRESSION))
    parser.add_argument("--trials", type=int)
    parser.add_argument("--docker", action="store_true", help="run tests and bash in Docker Sandbox (default)")
    parser.add_argument(
        "--unsafe-trusted-host",
        action="store_true",
        help="run a trusted local fixture on the host; disables the Docker safety boundary",
    )
    parser.add_argument("--bash", action="store_true", help="include a replayed Docker bash call")
    parser.add_argument("--adapter", default="react-agent", help="registered Agent adapter ID")
    parser.add_argument(
        "--s20-source",
        help="path to an external s20_comprehensive/code.py; required only when executing s20-replay",
    )
    parser.add_argument("--agent-version", help="Agent implementation version; defaults to the adapter version")
    parser.add_argument("--resume", action="store_true", help="reuse completed jobs and rerun incomplete jobs")
    parser.add_argument(
        "--rerun-invalid",
        action="store_true",
        help="with --resume, archive completed-but-invalid jobs and execute them again",
    )
    parser.add_argument("--dry-run", action="store_true", help="only validate and print expanded jobs")
    args = parser.parse_args()
    use_docker = not args.unsafe_trusted_host
    if args.bash and not use_docker:
        parser.error("--bash requires Docker; --unsafe-trusted-host is incompatible")
    try:
        adapter = get_adapter(args.adapter)
    except AdapterError as exc:
        parser.error(str(exc))
    agent_version = args.agent_version or adapter.default_version
    s20_source: Path | None = None
    if adapter.adapter_id == "s20-replay" and not args.dry_run:
        if not args.s20_source:
            parser.error("--s20-source is required when executing the optional s20-replay bridge")
        s20_source = Path(args.s20_source).expanduser().resolve()
        if not s20_source.is_file():
            parser.error(f"s20 source does not exist: {s20_source}")

    manifest_path = Path(args.manifest).resolve()
    try:
        manifest = load_manifest(manifest_path)
        validation = validate_manifest(manifest, args.project_root)
        if not validation.valid:
            print(json.dumps(validation.as_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
            return 2
        jobs = expand_trials(manifest, args.project_root, args.trials)
    except (OSError, ManifestError) as exc:
        print(f"MANIFEST ERROR: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(json.dumps({"manifest": manifest["id"], "jobs": jobs}, ensure_ascii=False, indent=2))
        return 0
    if use_docker:
        available, detail = DockerSandbox.available()
        if not available:
            print(f"DOCKER UNAVAILABLE: {detail}", file=sys.stderr)
            return 2

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_store = output_dir / "runs.db"
    summaries: list[dict[str, object]] = []
    for job in jobs:
        fingerprint = _job_fingerprint(
            job, adapter_id=adapter.adapter_id, agent_version=agent_version,
            use_docker=use_docker, replay_bash=args.bash,
        )
        try:
            job_dir = safe_child_path(output_dir, job["job_id"], "job_id")
        except ManifestError as exc:
            print(f"OUTPUT PATH ERROR: {exc}", file=sys.stderr)
            return 2
        if job_dir.exists():
            if not _owned_job_dir(job_dir, str(job["job_id"]), fingerprint):
                print(f"REFUSING UNOWNED OUTPUT DIRECTORY: {job_dir}", file=sys.stderr)
                return 2
            existing_result = job_dir / "result.json"
            if args.resume and existing_result.exists():
                result = json.loads(existing_result.read_text(encoding="utf-8"))
                trace_valid = (result.get("trace_validation") or {}).get("valid") is True
                evaluation_passed = (result.get("evaluation") or {}).get("passed") is True
                if result.get("status") == "completed" and evaluation_passed and trace_valid:
                    summaries.append(_job_summary(job, result))
                    continue
                if result.get("status") == "completed" and not args.rerun_invalid:
                    # Preserve an actual Agent failure as evidence unless the
                    # operator explicitly asks to retry invalid output.
                    summaries.append(_job_summary(job, result))
                    continue
            if args.resume:
                if existing_result.exists() and args.rerun_invalid:
                    archive_root = output_dir / "invalid-attempts"
                    archive_root.mkdir(exist_ok=True)
                    # Keep the archive name inside the same strict identifier
                    # limit as every runner-owned directory.
                    archive_name = f"retry_{int(time.time() * 1000)}_{str(job['job_id'])[:32]}"
                    archive_dir = safe_child_path(archive_root, archive_name, "archive job id")
                    shutil.move(str(job_dir), str(archive_dir))
                else:
                    shutil.rmtree(job_dir)
            else:
                print(f"OUTPUT EXISTS: {job_dir}", file=sys.stderr)
                return 2
        _create_job_dir(job_dir, str(job["job_id"]), fingerprint)
        worktree = job_dir / "worktree"
        shutil.copytree(str(job["fixture_path"]), worktree)
        git("init", cwd=worktree)
        git("config", "user.email", "regression-lab@example.invalid", cwd=worktree)
        git("config", "user.name", "Regression Lab", cwd=worktree)
        git("add", ".", cwd=worktree)
        git("commit", "-m", "benchmark fixture baseline", cwd=worktree)

        test_command = str(job["test_command"])
        if not use_docker and test_command.startswith("python "):
            test_command = "python3.11 " + test_command[len("python "):]
        spec = {
            "trial_id": str(job["job_id"]),
            "agent_version": agent_version,
            "adapter": adapter.as_spec(),
            "adapter_id": adapter.adapter_id,
            "case_id": job["case_id"],
            "prompt": job["prompt"],
            "worktree": str(worktree),
            "test_command": test_command,
            "sandbox": {**job["sandbox"], "image": "python:3.11-slim"} if use_docker else None,
            "replay_bash": args.bash,
            "allowed_paths": job["allowed_paths"],
            "forbidden_paths": job["forbidden_paths"],
            "allowed_tools": job["tool_policy"]["allow"],
            "denied_tools": job["tool_policy"]["deny"],
            "failure_mode": job.get("failure_mode"),
            "budget": job["budget"],
            "max_tokens": job["max_tokens"],
            "trace_output": str(job_dir / "trace.jsonl"),
            "result_output": str(job_dir / "result.json"),
            "run_store": str(run_store),
        }
        if s20_source is not None:
            spec["s20_source"] = str(s20_source)
        input_path = job_dir / "trial-input.json"
        input_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        timeout_seconds = int(job["sandbox"]["timeout_seconds"])
        completed = run_with_deadline(
            [sys.executable, str(adapter.worker_path), "--input", str(input_path)],
            cwd=REGRESSION,
            env={**os.environ, "PYTHONPATH": str(REGRESSION / "src")},
            timeout_seconds=timeout_seconds,
        )
        if completed.stdout:
            print(completed.stdout)
        if completed.stderr:
            print(completed.stderr, file=sys.stderr)
        result_path = job_dir / "result.json"
        if completed.timed_out:
            result = _timed_out_result(job, timeout_seconds)
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                result = {
                    **_timed_out_result(job, timeout_seconds),
                    "status": "infra_failed",
                    "error": f"worker exited without a valid result: {type(exc).__name__}: {exc}",
                }
                result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        summaries.append(_job_summary(job, result))

    summary = {"manifest": manifest["id"], "job_count": len(jobs), "jobs": summaries}
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(job["status"] == "completed" and job["evaluation_passed"] for job in summaries) else 1


def _job_summary(job: dict[str, object], result: dict[str, object]) -> dict[str, object]:
    scores = {score.get("evaluator"): score for score in result.get("scores", []) if isinstance(score, dict)}
    return {
        "job_id": job["job_id"],
        "case_id": job["case_id"],
        "trial_index": job["trial_index"],
        "status": result.get("status"),
        "evaluation_passed": result.get("evaluation", {}).get("passed", False),
        "test_passed": scores.get("test", {}).get("passed", False),
        "tool_calls": scores.get("tool_integrity", {}).get("actual", {}).get("tool_calls", 0),
        "duration_ms": scores.get("budget", {}).get("actual", {}).get("duration_ms", 0),
        "added_lines": scores.get("diff", {}).get("actual", {}).get("added_lines", 0),
        "deleted_lines": scores.get("diff", {}).get("actual", {}).get("deleted_lines", 0),
        "model_tokens": result.get("model_usage", {}).get("total_tokens", 0),
        "error": result.get("error"),
        "trace_id": result.get("trace_id"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
