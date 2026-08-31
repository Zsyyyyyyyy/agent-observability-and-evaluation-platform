#!/usr/bin/env python3
"""展开并执行确定性的 Benchmark Case Manifest。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_lab.manifest import (
    ManifestError,
    expand_trials,
    load_manifest,
    safe_child_path,
    validate_manifest,
)
from regression_lab.adapters import AdapterCapabilities, AdapterDescriptor, AdapterError, get_adapter
from regression_lab.attempts import AttemptManager, AttemptPaths, terminal_status_from_result
from regression_lab.attribution import attribute_trial
from regression_lab.behavior import summarize_trial_behavior
from regression_lab.behavior_diff import snapshot_trial_behavior
from regression_lab.runner import run_with_deadline
from regression_lab.sandbox import DockerSandbox
from regression_lab.store import RunStore
from regression_lab.artifacts import write_json_atomically
from regression_lab.protocol import agent_source_snapshot
from regression_lab.paths import asset_root, python_import_root, runtime_root


REGRESSION = asset_root()


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _job_fingerprint(
    job: dict[str, object], *, adapter_id: str, agent_version: str, use_docker: bool, replay_bash: bool, external_command: list[str] | None = None,
    expected_agent_source_hash: str | None = None, adapter_capabilities: dict[str, object] | None = None,
    external_observation_mode: str = "sdk",
    external_source_root: str | None = None,
) -> str:
    payload = {
        "job": job,
        "adapter_id": adapter_id,
        "agent_version": agent_version,
        "execution_mode": "docker" if use_docker else "unsafe_trusted_host",
        "replay_bash": replay_bash,
        "external_command": external_command,
        "expected_agent_source_hash": expected_agent_source_hash,
        "adapter_capabilities": adapter_capabilities,
        "external_observation_mode": external_observation_mode,
        "external_source_root": external_source_root,
        "external_command_source_hash": _external_command_source_hash(external_command, external_source_root),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _external_command_source_hash(command: list[str] | None, source_root: str | None = None) -> str | None:
    """优先计算 Agent 工作树哈希，否则计算入口文件哈希。"""

    return agent_source_snapshot(command or [], source_root)["agent_source_hash"]


def _owned_job_dir(job_dir: Path, job_id: str, fingerprint: str) -> bool:
    marker = job_dir / "run-manifest.json"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload == {"schema_version": 2, "job_id": job_id, "fingerprint": fingerprint}


def _create_job_dir(job_dir: Path, job_id: str, fingerprint: str) -> None:
    job_dir.mkdir(parents=True)
    write_json_atomically(job_dir / "run-manifest.json", {"schema_version": 2, "job_id": job_id, "fingerprint": fingerprint})


def _timed_out_result(job: dict[str, object], timeout_seconds: int) -> dict[str, object]:
    """Worker 超过 Trial 时限时，仍生成可持久化的失败结果。"""

    trace_id = "trace_parent_timeout_" + hashlib.sha256(
        str(job["job_id"]).encode("utf-8")
    ).hexdigest()[:12]
    return {
        "trial_id": job["job_id"],
        "status": "timed_out",
        # Worker 来不及发布 Trace；平台生成的身份让终态 Attempt 仍可进入 RunStore，
        # 同时由 trace_validation 明确记录证据缺失并保持失败关闭。
        "trace_id": trace_id,
        "trace_validation": {
            "valid": False,
            "trace_id": trace_id,
            "event_count": 0,
            "span_count": 0,
            "errors": ["worker deadline elapsed before Trace publication"],
        },
        "error": f"parent runner deadline exceeded ({timeout_seconds}s)",
        "test_exit_code": -1,
        "scores": [],
        "evaluation": {"passed": False, "reason": "worker deadline exceeded"},
    }


def _is_reusable_result(result: dict[str, object]) -> bool:
    trace_valid = (result.get("trace_validation") or {}).get("valid") is True
    evaluation_passed = (result.get("evaluation") or {}).get("passed") is True
    return result.get("status") == "completed" and evaluation_passed and trace_valid


def _publish_existing_attempt(job_dir: Path, attempts: AttemptManager) -> dict[str, object] | None:
    """根据选定 Attempt 投影恢复 Job 级兼容结果。"""

    selected = attempts.resolve_selected_attempt() or attempts.select_latest_terminal_attempt()
    if selected is None:
        return None
    attempt, result = selected
    _write_selected_result(job_dir, attempt, result)
    return result


def _sync_selected_store(run_store: Path, attempts: AttemptManager, attempt: AttemptPaths, result: dict[str, object]) -> None:
    """只有 Artifact 选择器确定 Trial 视图后，才发布 SQLite 投影。"""

    scores = [score for score in result.get("scores", []) if isinstance(score, dict)]
    RunStore(run_store).record_selected_projection(result, scores, attempt.attempt_id)


def _may_retry_model_failure(attempts: AttemptManager, result: dict[str, object], max_retries: int) -> bool:
    """只允许在上限内重试模型服务侧故障。"""

    if result.get("status") != "model_failed":
        return False
    # max_retries 不包含首次执行。
    return len(attempts.list_attempts()) < max_retries + 1


def _write_selected_result(job_dir: Path, attempt: AttemptPaths, result: dict[str, object]) -> None:
    """通过旧的 Job 级结果路径发布选定 Attempt。"""

    selected = {
        **result,
        "attempt_id": attempt.attempt_id,
        "attempt_path": str(attempt.directory),
    }
    write_json_atomically(job_dir / "result.json", selected)


def _prepare_worktree(job: dict[str, object], attempt: AttemptPaths) -> None:
    """为一个 Attempt 创建独立工作目录和固定的 Git 基线。"""

    shutil.copytree(str(job["fixture_path"]), attempt.worktree)
    git("init", cwd=attempt.worktree)
    git("config", "user.email", "regression-lab@example.invalid", cwd=attempt.worktree)
    git("config", "user.name", "Regression Lab", cwd=attempt.worktree)
    git("add", ".", cwd=attempt.worktree)
    git("commit", "-m", "benchmark fixture baseline", cwd=attempt.worktree)


def _build_trial_spec(
    job: dict[str, object],
    attempt: AttemptPaths,
    *,
    args: argparse.Namespace,
    adapter: AdapterDescriptor,
    adapter_capabilities: AdapterCapabilities,
    agent_version: str,
    use_docker: bool,
    external_command: list[str] | None,
    replay_source: Path | None,
) -> dict[str, object]:
    """把 CLI 配置和 Job 定义冻结为一次 Worker 输入。"""

    test_command = str(job["test_command"])
    if not use_docker and test_command.startswith("python "):
        test_command = "python3.11 " + test_command[len("python "):]
    spec: dict[str, object] = {
        "trial_id": str(job["job_id"]),
        "agent_version": agent_version,
        "agent_profile": args.agent_profile,
        "adapter": adapter.as_spec(),
        "adapter_id": adapter.adapter_id,
        "adapter_capabilities": adapter_capabilities.as_dict(),
        "observation_mode": args.external_observation_mode if adapter.adapter_id == "external-command" else None,
        "case_id": job["case_id"],
        "prompt": job["prompt"],
        "worktree": str(attempt.worktree),
        "test_command": test_command,
        "sandbox": {**job["sandbox"], "image": "python:3.11-slim"} if use_docker else None,
        # Docker 只决定测试隔离方式；Trial 时限始终来自 Benchmark。
        "trial_timeout_seconds": int(job["sandbox"]["timeout_seconds"]),
        "replay_bash": args.bash,
        "allowed_paths": job["allowed_paths"],
        "forbidden_paths": job["forbidden_paths"],
        "allowed_tools": job["tool_policy"]["allow"],
        "denied_tools": job["tool_policy"]["deny"],
        "required_evaluators": job["required_evaluators"],
        "acceptance_must": job["acceptance_must"],
        "failure_mode": job.get("failure_mode"),
        "budget": job["budget"],
        "max_tokens": job["max_tokens"],
        "attempt_id": attempt.attempt_id,
        "trace_output": str(attempt.trace),
        "result_output": str(attempt.result),
        # Worker 产物是不可变 Attempt 证据；selected-attempt.json 落盘后才写 SQLite 投影。
        "run_store": None,
        "protocol_fingerprint": args.protocol_fingerprint,
        "schedule_index": args.schedule_index,
        "expected_agent_source_hash": args.expected_agent_source_hash,
        "expected_runtime_environment_hash": args.expected_runtime_environment_hash,
    }
    if external_command is not None:
        spec["external_command"] = external_command
        if args.external_source_root:
            spec["agent_source_root"] = args.external_source_root
    if replay_source is not None:
        spec["replay_source"] = str(replay_source)
    return spec


def _load_worker_result(
    attempt: AttemptPaths,
    job: dict[str, object],
    timeout_seconds: int,
    *,
    timed_out: bool,
) -> dict[str, object]:
    """读取 Worker 结果；超时或无效输出统一转为平台失败证据。"""

    if timed_out:
        result = _timed_out_result(job, timeout_seconds)
        result["trace_path"] = str(attempt.trace)
        result["attempt_id"] = attempt.attempt_id
        write_json_atomically(attempt.result, result)
        return result
    try:
        return json.loads(attempt.result.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result = {
            **_timed_out_result(job, timeout_seconds),
            "status": "infra_failed",
            "error": f"worker exited without a valid result: {type(exc).__name__}: {exc}",
            "trace_path": str(attempt.trace),
            "attempt_id": attempt.attempt_id,
        }
        write_json_atomically(attempt.result, result)
        return result


def _merge_job_summaries(
    summary_path: Path, new_summaries: list[dict[str, object]]
) -> list[dict[str, object]]:
    """合并本次 Job 与已有 summary，支持按 Trial 子集分批运行。"""

    existing_jobs: dict[str, dict[str, object]] = {}
    if summary_path.exists():
        try:
            existing = json.loads(summary_path.read_text(encoding="utf-8"))
            existing_jobs = {
                str(item.get("job_id")): item
                for item in existing.get("jobs", [])
                if isinstance(item, dict) and isinstance(item.get("job_id"), str)
            }
        except (OSError, json.JSONDecodeError):
            existing_jobs = {}
    existing_jobs.update({str(item["job_id"]): item for item in new_summaries})
    return sorted(
        existing_jobs.values(),
        key=lambda item: (int(item.get("trial_index", 0)), str(item.get("job_id", ""))),
    )


def _build_parser() -> argparse.ArgumentParser:
    """定义单个 Benchmark 执行入口的命令行参数。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", default=str(runtime_root() / "benchmark"))
    parser.add_argument("--project-root", default=str(REGRESSION))
    parser.add_argument("--trials", type=int)
    parser.add_argument("--trial-index", type=int, action="append",
                        help="run only a selected 1-based Trial index; repeatable for orchestration")
    parser.add_argument("--docker", action="store_true", help="run tests and bash in Docker Sandbox (default)")
    parser.add_argument(
        "--unsafe-trusted-host",
        action="store_true",
        help="run a trusted local fixture on the host; disables the Docker safety boundary",
    )
    parser.add_argument("--bash", action="store_true", help="include a replayed Docker bash call")
    parser.add_argument("--adapter", default="react-agent", help="registered Agent adapter ID")
    parser.add_argument(
        "--replay-source",
        help="path to an external agent_entry.py; required only when executing readonly-replay",
    )
    parser.add_argument("--agent-version", help="Agent implementation version; defaults to the adapter version")
    parser.add_argument("--agent-profile", help="optional Agent operating-profile label recorded in the Trial")
    parser.add_argument("--external-command", help="JSON argv array for external-command, e.g. '[\"python3\", \"/path/agent.py\"]'")
    parser.add_argument("--external-source-root", help="platform-owned Agent source root used for module imports and identity")
    parser.add_argument("--adapter-capabilities", help="Evidence Capability JSON snapshot for an external-command Agent")
    parser.add_argument(
        "--external-observation-mode", choices=("sdk", "blackbox", "langgraph"), default="sdk",
        help="external-command evidence mode; sdk remains the legacy default",
    )
    parser.add_argument("--expected-agent-source-hash", help="frozen external Agent entry-point hash from the Experiment Protocol")
    parser.add_argument("--expected-runtime-environment-hash", help="frozen external Agent runtime environment identity hash")
    parser.add_argument("--resume", action="store_true", help="reuse completed jobs and rerun incomplete jobs")
    parser.add_argument(
        "--rerun-invalid",
        action="store_true",
        help="with --resume, archive completed-but-invalid jobs and execute them again",
    )
    parser.add_argument(
        "--rerun-completed",
        action="store_true",
        help="with --resume, create a new Attempt for selected completed jobs; preserve prior evidence",
    )
    parser.add_argument("--dry-run", action="store_true", help="only validate and print expanded jobs")
    parser.add_argument("--quiet", action="store_true", help="suppress successful JSON output")
    parser.add_argument("--protocol-fingerprint", help="platform-owned Experiment Protocol identity")
    parser.add_argument("--schedule-index", type=int, help="platform-owned interleaved execution position")
    return parser


def _resolve_adapter_config(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> tuple[AdapterDescriptor, str, list[str] | None, AdapterCapabilities, Path | None]:
    """校验 Adapter 相关 CLI 参数并返回规范化执行配置。"""

    try:
        adapter = get_adapter(args.adapter)
    except AdapterError as exc:
        parser.error(str(exc))
    agent_version = args.agent_version or adapter.default_version

    external_command: list[str] | None = None
    if args.external_command:
        try:
            decoded = json.loads(args.external_command)
        except json.JSONDecodeError as exc:
            parser.error(f"--external-command must be a JSON argv array: {exc.msg}")
        if not isinstance(decoded, list) or not decoded or not all(
            isinstance(item, str) and item for item in decoded
        ):
            parser.error("--external-command must be a non-empty JSON argv string array")
        external_command = decoded
    if adapter.adapter_id == "external-command" and not external_command:
        parser.error("--adapter external-command requires --external-command")
    if adapter.adapter_id != "external-command" and external_command:
        parser.error("--external-command is only valid with --adapter external-command")
    if adapter.adapter_id != "external-command" and args.external_observation_mode != "sdk":
        parser.error("--external-observation-mode is only valid with --adapter external-command")
    if args.external_source_root:
        source_root = Path(args.external_source_root)
        if adapter.adapter_id != "external-command" or not source_root.is_absolute() or not source_root.is_dir():
            parser.error("--external-source-root must be an existing absolute directory for external-command")

    adapter_capabilities = adapter.evidence_capabilities
    langgraph_capabilities = AdapterCapabilities(
        trace=True, hierarchical_trace=True, model_usage=True, tool_trace=True,
        tool_semantics=False, test_trace=False, context_trace=False,
        workflow_trace=True, mcp_trace=False,
    )
    if args.adapter_capabilities:
        if adapter.adapter_id != "external-command":
            parser.error("--adapter-capabilities is only valid with --adapter external-command")
        try:
            declared_capabilities = json.loads(args.adapter_capabilities)
        except json.JSONDecodeError as exc:
            parser.error(f"--adapter-capabilities must be a JSON object: {exc.msg}")
        parsed_capabilities = AdapterCapabilities.from_snapshot(declared_capabilities)
        if parsed_capabilities is None:
            parser.error("--adapter-capabilities must provide every AdapterCapabilities boolean field")
        adapter_capabilities = parsed_capabilities
    if args.external_observation_mode == "langgraph":
        if adapter.adapter_id != "external-command":
            parser.error("--external-observation-mode is only valid with --adapter external-command")
        if args.adapter_capabilities and adapter_capabilities != langgraph_capabilities:
            parser.error("langgraph observation mode uses platform-defined capabilities")
        adapter_capabilities = langgraph_capabilities

    replay_source: Path | None = None
    if adapter.adapter_id == "readonly-replay" and not args.dry_run:
        if not args.replay_source:
            parser.error("--replay-source is required when executing the optional readonly-replay bridge")
        replay_source = Path(args.replay_source).expanduser().resolve()
        if not replay_source.is_file():
            parser.error(f"external Agent source does not exist: {replay_source}")
    return adapter, agent_version, external_command, adapter_capabilities, replay_source


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    use_docker = not args.unsafe_trusted_host
    if args.bash and not use_docker:
        parser.error("--bash requires Docker; --unsafe-trusted-host is incompatible")
    adapter, agent_version, external_command, adapter_capabilities, replay_source = (
        _resolve_adapter_config(parser, args)
    )

    manifest_path = Path(args.manifest).resolve()
    try:
        manifest = load_manifest(manifest_path)
        validation = validate_manifest(manifest, args.project_root)
        if not validation.valid:
            print(json.dumps(validation.as_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
            return 2
        jobs = expand_trials(manifest, args.project_root, args.trials)
        if args.trial_index:
            selected = set(args.trial_index)
            if any(index < 1 for index in selected):
                raise ManifestError("trial_index must be positive")
            jobs = [job for job in jobs if int(job["trial_index"]) in selected]
            if not jobs:
                raise ManifestError("selected trial_index does not exist in this manifest")
    except (OSError, ManifestError) as exc:
        print(f"MANIFEST ERROR: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        if not args.quiet:
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
            external_command=external_command,
            expected_agent_source_hash=args.expected_agent_source_hash,
            adapter_capabilities=adapter_capabilities.as_dict(),
            external_observation_mode=args.external_observation_mode,
            external_source_root=args.external_source_root,
        )
        try:
            job_dir = safe_child_path(output_dir, job["job_id"], "job_id")
        except ManifestError as exc:
            print(f"OUTPUT PATH ERROR: {exc}", file=sys.stderr)
            return 2
        attempts = AttemptManager(
            job_dir, job_id=str(job["job_id"]), fingerprint=fingerprint,
            protocol_fingerprint=args.protocol_fingerprint, schedule_index=args.schedule_index,
        )
        existed_before_lock = job_dir.exists()
        if not existed_before_lock:
            _create_job_dir(job_dir, str(job["job_id"]), fingerprint)
        try:
            attempts.acquire_trial_lock()
            attempts.recover_orphaned_attempts()
        except RuntimeError as exc:
            print(f"TRIAL LOCK ERROR: {exc}", file=sys.stderr)
            return 2
        if existed_before_lock:
            if not _owned_job_dir(job_dir, str(job["job_id"]), fingerprint):
                print(f"REFUSING UNOWNED OUTPUT DIRECTORY: {job_dir}", file=sys.stderr)
                attempts.release_trial_lock()
                return 2
            existing_result = job_dir / "result.json"
            if args.resume and existing_result.exists():
                # 旧选择器中，历史成功 Attempt 会压过后续瞬时失败；当前流程只读取
                # 显式 Artifact 投影，不在恢复时重新排序。
                result = _publish_existing_attempt(job_dir, attempts) or json.loads(existing_result.read_text(encoding="utf-8"))
                selected_attempt = attempts.resolve_selected_attempt()
                if selected_attempt is not None:
                    _sync_selected_store(run_store, attempts, selected_attempt[0], selected_attempt[1])
                if _is_reusable_result(result) and not args.rerun_completed:
                    summaries.append(_job_summary(job, result))
                    attempts.release_trial_lock()
                    continue
                retry_model_failure = _may_retry_model_failure(attempts, result, int(job.get("max_retries", 0)))
                # 操作者可显式重试任意不可复用证据（包括超时）；自动恢复只重试模型故障。
                retry_invalid_evidence = args.rerun_invalid
                if not retry_model_failure and not retry_invalid_evidence and not args.rerun_completed:
                    summaries.append(_job_summary(job, result))
                    attempts.release_trial_lock()
                    continue
                if result.get("status") == "completed" and not (args.rerun_invalid or args.rerun_completed):
                    # 除非操作者明确要求重试无效输出，否则保留真实 Agent 失败作为证据。
                    summaries.append(_job_summary(job, result))
                    attempts.release_trial_lock()
                    continue
            if not args.resume:
                print(f"OUTPUT EXISTS: {job_dir}", file=sys.stderr)
                attempts.release_trial_lock()
                return 2

        attempt = attempts.create_attempt()
        _prepare_worktree(job, attempt)
        spec = _build_trial_spec(
            job,
            attempt,
            args=args,
            adapter=adapter,
            adapter_capabilities=adapter_capabilities,
            agent_version=agent_version,
            use_docker=use_docker,
            external_command=external_command,
            replay_source=replay_source,
        )
        input_path = attempt.input
        write_json_atomically(input_path, spec)
        timeout_seconds = int(job["sandbox"]["timeout_seconds"])
        completed = run_with_deadline(
            [sys.executable, str(adapter.worker_path), "--input", str(input_path)],
            cwd=REGRESSION,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(python_import_root()), os.environ.get("PYTHONPATH", "")]))},
            # 外部 Worker 负责 Trial 时限并清理 Agent 进程组；父进程额外保留少量时间，
            # 使 Worker 能在硬停止前持久化终态 Attempt 证据。
            timeout_seconds=timeout_seconds + 5,
        )
        if completed.stdout:
            print(completed.stdout)
        if completed.stderr:
            print(completed.stderr, file=sys.stderr)
        result = _load_worker_result(
            attempt, job, timeout_seconds, timed_out=completed.timed_out
        )
        if args.protocol_fingerprint:
            # 协议身份由平台注入，不接受 Adapter 或外部 Agent 的自报值。
            result["protocol_fingerprint"] = args.protocol_fingerprint
        if external_command is not None:
            # Worker 会在运行 Agent 前独立测量源码；即使 Worker 提前崩溃，Runner 也保留一份值。
            result.setdefault("agent_source_hash", _external_command_source_hash(external_command, args.external_source_root))
            result["expected_agent_source_hash"] = args.expected_agent_source_hash
            result["agent_source_hash_matches_protocol"] = (
                isinstance(args.expected_agent_source_hash, str)
                and result.get("agent_source_hash") == args.expected_agent_source_hash
            )
        if args.schedule_index is not None:
            result["schedule_index"] = args.schedule_index
        result.setdefault("adapter_id", adapter.adapter_id)
        result.setdefault("adapter_capabilities", adapter_capabilities.as_dict())
        result["behavior"] = summarize_trial_behavior(result)
        # Adapter 负责原始结果，Runner 负责协议身份；选择 Attempt 前先持久化增强结果，
        # 保证 Attempt 与发布的 Job 结果携带相同冻结身份。
        write_json_atomically(attempt.result, result)
        attempts.finish_attempt(
            attempt,
            terminal_status_from_result(result),
            error=result.get("error") if isinstance(result.get("error"), str) else None,
        )
        # 新完成的物理执行会更新 Trial 投影；旧投影只在读取或恢复时复用，
        # 不能阻止后续终态 Attempt 发布。
        selected = attempts.select_latest_terminal_attempt()
        if selected is None:
            selected_result = result
        else:
            selected_attempt_paths, selected_result = selected
            _write_selected_result(job_dir, selected_attempt_paths, selected_result)
        selected_attempt = attempts.resolve_selected_attempt()
        if selected_attempt is not None:
            _sync_selected_store(run_store, attempts, selected_attempt[0], selected_attempt[1])
        summaries.append(_job_summary(job, selected_result))
        attempts.release_trial_lock()

    summary_path = output_dir / "summary.json"
    merged_jobs = _merge_job_summaries(summary_path, summaries)
    summary = {"manifest": manifest["id"], "job_count": len(merged_jobs), "jobs": merged_jobs}
    write_json_atomically(summary_path, summary)
    if not args.quiet:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(job["status"] == "completed" and job["evaluation_passed"] for job in summaries) else 1


def _job_summary(job: dict[str, object], result: dict[str, object]) -> dict[str, object]:
    scores = {score.get("evaluator"): score for score in result.get("scores", []) if isinstance(score, dict)}
    diff_violations = (scores.get("diff", {}).get("actual", {}) or {}).get("violations", [])
    actual_diff_policy_violation = any(
        isinstance(violation, str) and violation != "empty_diff"
        for violation in diff_violations
    )
    capabilities = AdapterCapabilities.from_snapshot(result.get("adapter_capabilities"))
    behavior = result.get("behavior") if isinstance(result.get("behavior"), dict) else summarize_trial_behavior(result)
    # 外部 black-box 仍会有平台生命周期 Trace，但这不能伪装成工具或模型证据。
    tool_calls = scores.get("tool_integrity", {}).get("actual", {}).get("tool_calls", 0)
    model_tokens = result.get("model_usage", {}).get("total_tokens", 0)
    if capabilities is not None and not capabilities.tool_trace:
        tool_calls = None
    if capabilities is not None and not capabilities.model_usage:
        model_tokens = None
    return {
        "job_id": job["job_id"],
        "case_id": job["case_id"],
        "trial_index": job["trial_index"],
        "status": result.get("status"),
        "evaluation_passed": result.get("evaluation", {}).get("passed", False),
        "trace_valid": (result.get("trace_validation") or {}).get("valid"),
        "test_passed": scores.get("test", {}).get("passed", False),
        "path_policy_passed": scores.get("path_policy", {}).get("passed"),
        # empty_diff 是模型或基础设施失败的预期下游现象，不属于策略违规；
        # DiffEvaluator 的其他违规仍保留策略含义，并计入 Gate 比率。
        "diff_policy_violated": actual_diff_policy_violation,
        "tool_calls": tool_calls,
        "duration_ms": scores.get("budget", {}).get("actual", {}).get("duration_ms", 0),
        "added_lines": scores.get("diff", {}).get("actual", {}).get("added_lines", 0),
        "deleted_lines": scores.get("diff", {}).get("actual", {}).get("deleted_lines", 0),
        "model_tokens": model_tokens,
        "adapter_id": result.get("adapter_id"),
        "adapter_capabilities": result.get("adapter_capabilities"),
        "behavior": behavior,
        "behavior_snapshot": snapshot_trial_behavior(result),
        "failure_attribution": result.get("failure_attribution") or attribute_trial(result),
        "error": result.get("error"),
        "trace_id": result.get("trace_id"),
        "agent_source_hash": result.get("agent_source_hash"),
        "expected_agent_source_hash": result.get("expected_agent_source_hash"),
        "agent_source_hash_matches_protocol": result.get("agent_source_hash_matches_protocol"),
        "runtime_environment_matches_protocol": result.get("runtime_environment_matches_protocol"),
        "evidence_provenance": result.get("evidence_provenance"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
