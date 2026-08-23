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
from tempfile import TemporaryDirectory
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_lab.agent_spec import AgentSpecError
from regression_lab.manifest import ManifestError, load_manifest, validate_manifest
from scripts.regression_lab import _console_port, _validate_experiment_specs


REGRESSION = Path(__file__).resolve().parents[1]
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


def _quick_spec_documents(request: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
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
    if mode not in {"blackbox", "sdk"}:
        raise ValueError("观测模式无效")

    def document(python_field: str, version_field: str, entrypoint_field: str, label: str) -> dict[str, object]:
        target = text(entrypoint_field, f"{label} {'模块名' if target_kind == 'module' else '入口文件'}")
        python = text(python_field, f"{label} Python 解释器路径")
        command = [python, "-m", target] if target_kind == "module" else [python, target]
        return {
            "schema_version": 1, "project_id": project_id,
            "agent": {"id": agent_id, "version": text(version_field, f"{label} 版本")},
            "runtime": {"command": [*command, "--workspace", "{workspace}", "--task", "{task}"]},
            "observation": {"mode": mode},
        }

    return document("baseline_python_executable", "baseline_version", "baseline_entrypoint", "Baseline"), document("candidate_python_executable", "candidate_version", "candidate_entrypoint", "Candidate")


def _load_quick_specs(request: dict[str, object]):
    baseline_document, candidate_document = _quick_spec_documents(request)
    with TemporaryDirectory() as directory:
        root = Path(directory)
        baseline_path, candidate_path = root / "baseline.json", root / "candidate.json"
        baseline_path.write_text(json.dumps(baseline_document), encoding="utf-8")
        candidate_path.write_text(json.dumps(candidate_document), encoding="utf-8")
        return _validate_experiment_specs(str(baseline_path), str(candidate_path))


def _prepared_request(request: dict[str, object]) -> dict[str, object]:
    if request.get("launch_mode") != "quick":
        return request
    documents = _quick_spec_documents(request)
    canonical = json.dumps(documents, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    spec_root = REGRESSION / ".runtime" / "studio" / "agent-specs" / hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    spec_root.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, document in zip(("baseline.json", "candidate.json"), documents):
        path = spec_root / name
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths.append(str(path))
    return {**request, "baseline": paths[0], "candidate": paths[1]}


def preflight(request: object) -> dict[str, object]:
    if not isinstance(request, dict):
        return {"valid": False, "errors": ["请求格式无效"]}
    errors: list[str] = []
    try:
        if request.get("launch_mode") == "quick":
            baseline, candidate = _load_quick_specs(request)
        else:
            baseline_path = _spec_path(request.get("baseline"), "Baseline")
            candidate_path = _spec_path(request.get("candidate"), "Candidate")
            baseline, candidate = _validate_experiment_specs(str(baseline_path), str(candidate_path))
    except (ValueError, AgentSpecError) as exc:
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
    if errors:
        return {"valid": False, "errors": errors, "warnings": warnings}
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
    command = [
        sys.executable, str(REGRESSION / "scripts" / "regression_lab.py"), "experiment", "run",
        "--baseline", str(Path(str(request["baseline"])).expanduser().resolve()),
        "--candidate", str(Path(str(request["candidate"])).expanduser().resolve()),
        "--trials", str(request["trials"]),
    ]
    for manifest in _selected_manifests(request["benchmarks"]):
        command.extend(["--benchmark", str(manifest)])
    if request.get("execution_mode") == "trusted_host":
        command.append("--unsafe-trusted-host")
    return command


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
        run.status = "completed" if returncode in {0, 1} else "failed"
        runtime = run.runtime
    if runtime and Path(runtime).is_dir():
        port = _console_port(None)
        console = subprocess.Popen(
            [sys.executable, str(REGRESSION / "scripts" / "serve_dashboard.py"), "--runtime", runtime, "--port", str(port)],
            cwd=REGRESSION, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        )
        with run.lock:
            run.console_process = console
            run.console_url = f"http://127.0.0.1:{port}"


def run_status(run: StudioRun) -> dict[str, object]:
    with run.lock:
        return {"status": run.status, "runtime": run.runtime, "console_url": run.console_url, "returncode": run.returncode, "logs": list(run.logs)}


def handler_for(static_root: Path, run: StudioRun):
    class StudioHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(static_root), **kwargs)

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
            if path != "/api/run": return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            checked = preflight(request)
            if not checked["valid"]:
                return self._json(checked, HTTPStatus.UNPROCESSABLE_ENTITY)
            assert isinstance(request, dict)
            prepared = _prepared_request(request)
            with run.lock:
                if run.process is not None and run.process.poll() is None:
                    return self._json({"error": "已有 Experiment 正在运行"}, HTTPStatus.CONFLICT)
                run.process = subprocess.Popen(command_for(prepared), cwd=REGRESSION, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                run.status, run.runtime, run.console_url, run.returncode, run.logs = "running", None, None, None, []
                threading.Thread(target=_read_run_output, args=(run,), daemon=True).start()
            return self._json(run_status(run), HTTPStatus.ACCEPTED)

        def log_message(self, format: str, *args: object) -> None:
            print("[studio] " + format % args)
    return StudioHandler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8764)
    args = parser.parse_args()
    # Studio 可以启动本机 Agent；不提供网络监听选项，避免无认证执行入口被误暴露。
    server = ThreadingHTTPServer((STUDIO_HOST, args.port), handler_for(REGRESSION / "web", StudioRun()))
    print(f"Run Studio: http://{STUDIO_HOST}:{args.port}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
