#!/usr/bin/env python3
"""Serve the local, controlled Experiment launch surface."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_lab.agent_spec import AgentSpecError
from regression_lab.git_sources import GitSourceError, GitSourcePlan, GitSourceSnapshots, create_git_source_snapshots, entry_exists, inspect_git_sources, module_exists
from regression_lab.manifest import ManifestError, load_manifest, validate_manifest
from regression_lab.paths import asset_path, asset_root, is_source_checkout, runtime_root
from regression_lab.artifacts import write_json_atomically
from regression_lab.runner import terminate_process_group
from scripts.regression_lab import _console_port, _validate_experiment_specs


REGRESSION = asset_root()
STUDIO_HOST = "127.0.0.1"
MAX_TRIALS = 10
MAX_LOG_LINES = 160


@dataclass
class StudioRun:
    process: subprocess.Popen[str] | None = None
    console_process: subprocess.Popen[str] | None = None
    status: str = "idle"
    runtime: str | None = None
    console_url: str | None = None
    returncode: int | None = None
    logs: list[str] = field(default_factory=list)
    source_snapshots: GitSourceSnapshots | None = None
    request: dict[str, object] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def benchmark_catalog() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted((REGRESSION / "benchmarks").glob("*.yaml")):
        try:
            manifest = load_manifest(path)
        except (OSError, ManifestError):
            continue
        rows.append({"id": path.name, "title": str(manifest.get("title") or path.stem), "case_id": str(manifest.get("id") or path.stem)})
    return rows


def _selected_manifests(values: object) -> list[Path]:
    if not isinstance(values, list) or not values or not all(isinstance(item, str) for item in values):
        raise ValueError("至少选择一个 Benchmark Case")
    allowed = {item["id"]: REGRESSION / "benchmarks" / item["id"] for item in benchmark_catalog()}
    if len(values) != len(set(values)) or any(item not in allowed for item in values):
        raise ValueError("Benchmark 选择无效，请刷新页面后重试")
    return [allowed[item] for item in values]


def _spec_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} AgentSpec 路径不能为空")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"{label} AgentSpec 文件不存在")
    return path


def _quick_spec_documents(
    request: dict[str, object], source_roots: tuple[Path, Path] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    def text(field: str, label: str) -> str:
        value = request.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} 不能为空")
        return value.strip()

    project_id = text("project_id", "项目名称")
    agent_id = text("agent_id", "Agent 名称")
    target_kind = request.get("launch_target_kind", "script")
    if target_kind not in {"script", "module"}:
        raise ValueError("启动方式无效")
    mode = request.get("observation_mode", "blackbox")
    if mode not in {"blackbox", "sdk", "langgraph"}:
        raise ValueError("观测模式无效")

    source_mode = request.get("source_mode", "two_entries")
    if source_mode not in {"two_entries", "git_repository"}:
        raise ValueError("源码来源无效")

    def document(index: int, python_field: str, version_field: str, entrypoint_field: str, label: str) -> dict[str, object]:
        target = text(entrypoint_field, f"{label} {'模块名' if target_kind == 'module' else '入口文件'}")
        python = text(python_field, f"{label} Python 解释器路径")
        source_root = source_roots[index] if source_roots is not None else None
        if source_mode == "git_repository":
            if source_root is None:
                raise ValueError("Git 源码快照尚未创建")
            command = [python, "-m", target] if target_kind == "module" else [python, "{agent_source}/" + target]
        else:
            command = [python, "-m", target] if target_kind == "module" else [python, target]
        runtime: dict[str, object] = {"command": [*command, "--workspace", "{workspace}", "--task", "{task}"]}
        if source_root is not None:
            runtime["source_root"] = str(source_root)
        return {
            "schema_version": 1, "project_id": project_id,
            "agent": {"id": agent_id, "version": text(version_field, f"{label} 版本")},
            "runtime": runtime,
            "observation": {"mode": mode},
        }

    return (
        document(0, "baseline_python_executable", "baseline_version", "baseline_entrypoint", "Baseline"),
        document(1, "candidate_python_executable", "candidate_version", "candidate_entrypoint", "Candidate"),
    )


def _load_quick_specs(request: dict[str, object]):
    baseline_document, candidate_document = _quick_spec_documents(request)
    with TemporaryDirectory() as directory:
        root = Path(directory)
        baseline_path, candidate_path = root / "baseline.json", root / "candidate.json"
        baseline_path.write_text(json.dumps(baseline_document), encoding="utf-8")
        candidate_path.write_text(json.dumps(candidate_document), encoding="utf-8")
        return _validate_experiment_specs(str(baseline_path), str(candidate_path))


def _validate_git_quick_specs(request: dict[str, object]) -> None:
    """Git 预检不创建 clone，但仍应尽早校验版本身份和解释器。"""

    with TemporaryDirectory() as directory:
        root = Path(directory)
        baseline_document, candidate_document = _quick_spec_documents(request, (root, root))
        baseline_path, candidate_path = root / "baseline.json", root / "candidate.json"
        baseline_path.write_text(json.dumps(baseline_document), encoding="utf-8")
        candidate_path.write_text(json.dumps(candidate_document), encoding="utf-8")
        _validate_experiment_specs(str(baseline_path), str(candidate_path))


def _git_plan(request: dict[str, object]) -> GitSourcePlan:
    if request.get("source_mode", "two_entries") != "git_repository":
        raise ValueError("Git 来源未启用")
    repository = request.get("repository_path")
    baseline_ref = request.get("baseline_ref")
    candidate_source = request.get("candidate_source", "working_tree")
    candidate_ref = request.get("candidate_ref")
    if not isinstance(repository, str) or not isinstance(baseline_ref, str):
        raise ValueError("Git 仓库路径和 Baseline ref 不能为空")
    plan = inspect_git_sources(repository, baseline_ref, str(candidate_source), candidate_ref if isinstance(candidate_ref, str) else None)
    if request.get("launch_target_kind", "script") == "script":
        baseline_entry = request.get("baseline_entrypoint")
        candidate_entry = request.get("candidate_entrypoint")
        if not isinstance(baseline_entry, str) or not entry_exists(plan, baseline_entry, candidate=False):
            raise ValueError("Baseline 入口文件不存在于指定 Git 版本中")
        if not isinstance(candidate_entry, str) or not entry_exists(plan, candidate_entry, candidate=True):
            raise ValueError("Candidate 入口文件不存在于指定 Git 版本中")
    else:
        baseline_module = request.get("baseline_entrypoint")
        candidate_module = request.get("candidate_entrypoint")
        if not isinstance(baseline_module, str) or not module_exists(plan, baseline_module, candidate=False):
            raise ValueError("Baseline 模块不存在于指定 Git 版本中")
        if not isinstance(candidate_module, str) or not module_exists(plan, candidate_module, candidate=True):
            raise ValueError("Candidate 模块不存在于指定 Git 版本中")
    return plan


def _prepared_request_with_snapshots(request: dict[str, object]) -> tuple[dict[str, object], GitSourceSnapshots | None]:
    if request.get("launch_mode") != "quick":
        return request, None
    snapshots = None
    if request.get("source_mode", "two_entries") == "git_repository":
        snapshots = create_git_source_snapshots(_git_plan(request))
    try:
        roots = (snapshots.baseline_root, snapshots.candidate_root) if snapshots else None
        documents = _quick_spec_documents(request, roots)
    except Exception:
        if snapshots:
            snapshots.cleanup()
        raise
    canonical = json.dumps(documents, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # AgentSpec 是用户输入，不能写入随 pip 安装的只读包目录。
    spec_root = (Path(snapshots.directory.name) / "agent-specs") if snapshots else runtime_root() / "studio" / "agent-specs" / hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    spec_root.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, document in zip(("baseline.json", "candidate.json"), documents):
        path = spec_root / name
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths.append(str(path))
    return {**request, "baseline": paths[0], "candidate": paths[1]}, snapshots


def _prepared_request(request: dict[str, object]) -> dict[str, object]:
    """兼容原有测试和两入口流程；Git 快照生命周期由 Studio Run 持有。"""

    prepared, snapshots = _prepared_request_with_snapshots(request)
    if snapshots is not None:
        snapshots.cleanup()
        raise ValueError("Git sources require Studio run ownership")
    return prepared


def preflight(request: object) -> dict[str, object]:
    if not isinstance(request, dict):
        return {"valid": False, "errors": ["请求格式无效"]}
    errors: list[str] = []
    source_plan = None
    try:
        if request.get("launch_mode") == "quick" and request.get("source_mode", "two_entries") == "git_repository":
            source_plan = _git_plan(request)
            _validate_git_quick_specs(request)
            # Git 模式的 AgentSpec 依赖临时快照；预检仅校验可见输入，正式启动后再生成。
            baseline = candidate = None
        elif request.get("launch_mode") == "quick":
            baseline, candidate = _load_quick_specs(request)
        else:
            baseline_path = _spec_path(request.get("baseline"), "Baseline")
            candidate_path = _spec_path(request.get("candidate"), "Candidate")
            baseline, candidate = _validate_experiment_specs(str(baseline_path), str(candidate_path))
    except (ValueError, AgentSpecError, GitSourceError) as exc:
        errors.append(str(exc))
        baseline = candidate = None
    try:
        manifests = _selected_manifests(request.get("benchmarks"))
    except ValueError as exc:
        errors.append(str(exc))
        manifests = []
    else:
        for path in manifests:
            try:
                validation = validate_manifest(load_manifest(path), REGRESSION)
            except (OSError, ManifestError) as exc:
                errors.append(f"{path.name}: {exc}")
                continue
            if not validation.valid:
                errors.extend(f"{path.name}: {error}" for error in validation.errors)
    trials = request.get("trials")
    if isinstance(trials, bool) or not isinstance(trials, int) or not 1 <= trials <= MAX_TRIALS:
        errors.append(f"重复次数必须是 1 到 {MAX_TRIALS} 的整数")
    execution_mode = request.get("execution_mode", "docker")
    if execution_mode not in {"docker", "trusted_host"}:
        errors.append("执行环境无效")
    if execution_mode == "trusted_host" and request.get("trusted_host_confirmed") is not True:
        errors.append("请确认仅在可信主机上运行 Agent")
    warnings: list[str] = []
    if execution_mode == "docker" and shutil.which("docker") is None:
        errors.append("未检测到 Docker；请选择可信主机并确认风险，或先安装 Docker")
    if execution_mode == "trusted_host":
        warnings.append("Agent 与平台测试将在当前主机执行；仅适合你明确可信的本地命令。")
    observation_mode = request.get("observation_mode") if request.get("launch_mode") == "quick" else (baseline.observation_mode if baseline else None)
    if observation_mode == "langgraph":
        warnings.append("LangGraph 模式只需在 graph.invoke()/stream() 入口加入一次平台 Callback；无需 Agent Output 文件或节点级埋点。")
    if errors:
        return {"valid": False, "errors": errors, "warnings": warnings}
    if source_plan is not None:
        configuration = {
            "project_id": request.get("project_id"), "agent_id": request.get("agent_id"),
            "baseline_version": request.get("baseline_version"), "candidate_version": request.get("candidate_version"),
            "benchmark_count": len(manifests), "trial_count": len(manifests) * trials * 2,
            "execution_mode": execution_mode,
            "git_sources": {
                "baseline_revision": source_plan.baseline_revision,
                "candidate_revision": source_plan.candidate_revision,
                "candidate_dirty": source_plan.candidate_dirty,
                "tracked_changes": source_plan.tracked_change_count,
                "untracked_changes": source_plan.untracked_change_count,
            },
        }
        return {"valid": True, "errors": [], "warnings": [*warnings, "Git snapshots are created only after you start the Experiment; ignored files and common untracked local secret files are excluded."], "configuration": configuration}
    assert baseline is not None and candidate is not None
    return {
        "valid": True, "errors": [], "warnings": warnings,
        "configuration": {
            "project_id": baseline.project_id, "agent_id": baseline.agent_id,
            "baseline_version": baseline.version, "candidate_version": candidate.version,
            "benchmark_count": len(manifests), "trial_count": len(manifests) * trials * 2,
            "execution_mode": execution_mode,
        },
    }


def command_for(request: dict[str, object]) -> list[str]:
    # 源码模式保留可直接调试的脚本入口；wheel 中脚本不在资源目录，只能走模块入口。
    entrypoint = (
        [sys.executable, str(asset_path("scripts", "regression_lab.py"))]
        if is_source_checkout()
        else [sys.executable, "-m", "scripts.regression_lab"]
    )
    command = [
        *entrypoint, "experiment", "run",
        "--baseline", str(Path(str(request["baseline"])).expanduser().resolve()),
        "--candidate", str(Path(str(request["candidate"])).expanduser().resolve()),
        "--trials", str(request["trials"]),
    ]
    for manifest in _selected_manifests(request["benchmarks"]):
        command.extend(["--benchmark", str(manifest)])
    if request.get("execution_mode") == "trusted_host":
        command.append("--unsafe-trusted-host")
    runtime = request.get("studio_runtime")
    if isinstance(runtime, str):
        command.extend(["--output-dir", runtime])
    if request.get("resume") is True:
        command.append("--resume")
    return command


def _run_state(runtime: str | None, status: str) -> None:
    if not runtime:
        return
    root = Path(runtime)
    root.mkdir(parents=True, exist_ok=True)
    write_json_atomically(root / "experiment-state.json", {"schema_version": 1, "status": status, "updated_at": datetime.now(timezone.utc).isoformat()})


def _runtime_for(request: dict[str, object]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    project = str(request.get("project_id") or "studio")
    agent = str(request.get("agent_id") or "agent")
    baseline = str(request.get("baseline_version") or "baseline")
    candidate = str(request.get("candidate_version") or "candidate")
    return str(runtime_root() / "projects" / project / "experiments" / f"{agent}-{baseline}-vs-{candidate}-{stamp}")


def _read_run_output(run: StudioRun) -> None:
    assert run.process is not None and run.process.stdout is not None
    for line in run.process.stdout:
        text = line.rstrip()
        with run.lock:
            run.logs.append(text)
            run.logs[:] = run.logs[-MAX_LOG_LINES:]
            if text.startswith("Runtime: "):
                run.runtime = text.removeprefix("Runtime: ")
    returncode = run.process.wait()
    with run.lock:
        run.returncode = returncode
        run.status = "completed" if returncode in {0, 1} else "cancelled" if returncode < 0 else "failed"
        runtime = run.runtime
    _run_state(runtime, run.status)
    try:
        if runtime and Path(runtime).is_dir():
            port = _console_port(None)
            console = subprocess.Popen(
                [sys.executable, "-m", "scripts.serve_dashboard", "--runtime", runtime, "--port", str(port)],
                cwd=REGRESSION, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
            )
            with run.lock:
                run.console_process = console
                run.console_url = f"http://127.0.0.1:{port}"
    finally:
        if run.source_snapshots is not None:
            run.source_snapshots.cleanup()
            run.source_snapshots = None


def run_status(run: StudioRun) -> dict[str, object]:
    with run.lock:
        return {"status": run.status, "runtime": run.runtime, "console_url": run.console_url, "returncode": run.returncode, "logs": list(run.logs)}


def handler_for(static_root: Path, run: StudioRun):
    class StudioHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(static_root), **kwargs)

        def log_message(self, format: str, *args: object) -> None:
            # Studio 每两秒轮询一次运行状态；成功响应不应淹没真正的 Agent 日志。
            if self.command == "GET" and urlparse(self.path).path == "/api/run":
                return
            super().log_message(format, *args)

        def _json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def _request_body(self) -> object:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                return json.loads(self.rfile.read(length))
            except (ValueError, json.JSONDecodeError):
                return None

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/catalog": return self._json({"benchmarks": benchmark_catalog(), "max_trials": MAX_TRIALS, "python_executable": sys.executable})
            if path == "/api/run": return self._json(run_status(run))
            if path == "/": self.path = "/studio.html"
            return super().do_GET()

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            request = self._request_body()
            if path == "/api/preflight": return self._json(preflight(request))
            if path == "/api/run/cancel":
                with run.lock:
                    process = run.process
                    if process is None or process.poll() is not None:
                        return self._json({"error": "没有正在运行的 Experiment"}, HTTPStatus.CONFLICT)
                    run.status = "cancelling"
                    _run_state(run.runtime, "cancelling")
                terminate_process_group(process, grace_seconds=5)
                return self._json(run_status(run), HTTPStatus.ACCEPTED)
            if path == "/api/run/resume":
                with run.lock:
                    previous, runtime = dict(run.request or {}), run.runtime
                    active = run.process is not None and run.process.poll() is None
                if active or not previous or not runtime:
                    return self._json({"error": "没有可恢复的已取消 Experiment"}, HTTPStatus.CONFLICT)
                try:
                    prepared, snapshots = _prepared_request_with_snapshots(previous)
                except (ValueError, AgentSpecError, GitSourceError) as exc:
                    return self._json({"valid": False, "errors": [str(exc)]}, HTTPStatus.UNPROCESSABLE_ENTITY)
                prepared = {**prepared, "studio_runtime": runtime, "resume": True}
                try:
                    process = subprocess.Popen(command_for(prepared), cwd=REGRESSION, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
                except OSError:
                    if snapshots:
                        snapshots.cleanup()
                    raise
                with run.lock:
                    run.process, run.source_snapshots = process, snapshots
                    run.status, run.returncode, run.logs = "running", None, []
                    _run_state(runtime, "running")
                    threading.Thread(target=_read_run_output, args=(run,), daemon=True).start()
                return self._json(run_status(run), HTTPStatus.ACCEPTED)
            if path != "/api/run": return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            checked = preflight(request)
            if not checked["valid"]:
                return self._json(checked, HTTPStatus.UNPROCESSABLE_ENTITY)
            assert isinstance(request, dict)
            with run.lock:
                if run.process is not None and run.process.poll() is None:
                    return self._json({"error": "已有 Experiment 正在运行"}, HTTPStatus.CONFLICT)
            try:
                prepared, snapshots = _prepared_request_with_snapshots(request)
            except (ValueError, AgentSpecError, GitSourceError) as exc:
                return self._json({"valid": False, "errors": [str(exc)]}, HTTPStatus.UNPROCESSABLE_ENTITY)
            with run.lock:
                if run.process is not None and run.process.poll() is None:
                    if snapshots:
                        snapshots.cleanup()
                    return self._json({"error": "已有 Experiment 正在运行"}, HTTPStatus.CONFLICT)
                runtime = _runtime_for(request)
                prepared = {**prepared, "studio_runtime": runtime}
                try:
                    run.process = subprocess.Popen(command_for(prepared), cwd=REGRESSION, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
                except OSError:
                    if snapshots:
                        snapshots.cleanup()
                    raise
                run.source_snapshots, run.request = snapshots, request
                run.status, run.runtime, run.console_url, run.returncode, run.logs = "running", runtime, None, None, []
                _run_state(runtime, "running")
                threading.Thread(target=_read_run_output, args=(run,), daemon=True).start()
            return self._json(run_status(run), HTTPStatus.ACCEPTED)

    return StudioHandler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8764)
    args = parser.parse_args()
    # Studio 可以启动本机 Agent；不提供网络监听选项，避免无认证执行入口被误暴露。
    server = ThreadingHTTPServer((STUDIO_HOST, args.port), handler_for(asset_path("web"), StudioRun()))
    print(f"Run Studio: http://{STUDIO_HOST}:{args.port}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
