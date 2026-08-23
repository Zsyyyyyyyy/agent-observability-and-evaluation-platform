#!/usr/bin/env python3
"""Friendly local entry point for Regression Lab onboarding commands."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_lab.agent_spec import AgentSpecError, load_agent_spec
from regression_lab.gate import evaluate_gate
from regression_lab.integrity import verify_experiment_runtime
from regression_lab.protocol import write_json_atomically


def _validate_agent(path: str) -> int:
    try:
        spec = load_agent_spec(path)
    except AgentSpecError as exc:
        print("Agent spec validation failed:\n" + str(exc), file=sys.stderr)
        return 2
    config = spec.as_external_command_config()
    print("Agent spec is valid")
    print(f"  Project: {spec.project_id or 'unassigned (legacy-compatible)'}")
    print(f"  Agent: {spec.agent_id} · {spec.version}")
    print(f"  Launch: {' '.join(spec.command)}")
    print(f"  Observation: {spec.observation_mode}")
    print("  Adapter: external-command")
    if spec.observation_mode == "sdk":
        enabled = [name for name, value in config["adapter_capabilities"].items() if name != "schema_version" and value]
        print("  Evidence capability: " + ", ".join(enabled))
    print("No Agent process, model call, Trial, or Artifact was created.")
    return 0


def _score(result: dict[str, object], name: str) -> dict[str, object]:
    return next((item for item in result.get("scores", []) if isinstance(item, dict) and item.get("evaluator") == name), {})


def _mark(passed: bool) -> str:
    return "✓" if passed else "✗"


def _smoke_result(runtime: Path) -> dict[str, object] | None:
    summary_path = runtime / "summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        job = summary["jobs"][0]
        result_path = runtime / str(job["job_id"]) / "result.json"
        return json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, KeyError, IndexError, TypeError, json.JSONDecodeError):
        return None


def _print_smoke(runtime: Path, result: dict[str, object] | None) -> None:
    print("Agent Smoke\n")
    if result is None:
        print("✗ Trial Artifact was not created")
        print(f"Runtime: {runtime}")
        return
    lifecycle = result.get("process_lifecycle") if isinstance(result.get("process_lifecycle"), dict) else {}
    tests = _score(result, "test")
    trace = result.get("trace_validation") if isinstance(result.get("trace_validation"), dict) else {}
    print(f"{_mark(lifecycle.get('started') is True)} Agent command started")
    print(f"{_mark(lifecycle.get('status') == 'process_completed')} Agent process completed")
    print(f"{_mark(isinstance(result.get('git_evidence'), dict))} Git evidence collected")
    print(f"{_mark(tests.get('passed') is True)} Platform tests passed")
    print(f"{_mark(trace.get('valid') is True)} Lifecycle Trace valid\n")
    print("Observability")
    capabilities = result.get("adapter_capabilities") if isinstance(result.get("adapter_capabilities"), dict) else {}
    if result.get("observation_mode") == "blackbox":
        print("✓ process lifecycle")
    for label, capability in (("model usage", "model_usage"), ("tool trace", "tool_trace"), ("workflow trace", "workflow_trace")):
        value = capabilities.get(capability)
        print(f"{'✓' if value is True else '—'} {label}: {'available capability' if value is True else 'unsupported'}")
    evaluation = result.get("evaluation") if isinstance(result.get("evaluation"), dict) else {}
    print(f"\nEvaluation: {'PASS' if evaluation.get('passed') is True else 'FAIL'}")
    if evaluation.get("passed") is not True:
        error = result.get("error")
        if isinstance(error, str) and error:
            print(f"Reason: {error}")
        elif tests.get("passed") is not True:
            print("Reason: platform tests did not pass")
        elif trace.get("valid") is not True:
            print("Reason: lifecycle Trace validation failed")
    print(f"Runtime: {runtime}")


def _console_port(port: int | None) -> int:
    candidates = [port] if port is not None else list(range(8765, 8775))
    for candidate in candidates:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", candidate))
        except OSError:
            continue
        return candidate
    if port is None:
        raise ValueError("no free Console port found in 8765-8774")
    raise ValueError(f"Console port {port} is already in use")


def _serve_console(runtime: Path, port: int | None) -> int:
    try:
        selected_port = _console_port(port)
    except ValueError as exc:
        print(f"Console setup error: {exc}", file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{selected_port}"
    print(f"Observability Console: {url}")
    try:
        opened = webbrowser.open(url)
    except Exception:  # 浏览器只是便利入口，不能改变本地报告的完成状态。
        opened = False
    if not opened:
        print(f"Open this URL in a browser: {url}")
    try:
        return subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "serve_dashboard.py"),
             "--runtime", str(runtime), "--port", str(selected_port)],
            cwd=Path(__file__).resolve().parents[1], check=False,
        ).returncode
    except KeyboardInterrupt:
        # Console 是前台只读进程；Ctrl+C 应安静地结束它，而非泄漏调用栈。
        return 0


def _smoke_agent(path: str, benchmark: str | None, unsafe_trusted_host: bool,
                 *, open_console: bool, port: int | None) -> int:
    try:
        spec = load_agent_spec(path)
    except AgentSpecError as exc:
        print("Agent spec validation failed:\n" + str(exc), file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    manifest = Path(benchmark).resolve() if benchmark else root / "benchmarks" / "smoke-case-design.yaml"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    runtime = root / ".runtime" / "agent-smoke" / f"{spec.agent_id}-{spec.version}-{stamp}"
    config = spec.as_external_command_config()
    command = [
        sys.executable, str(root / "scripts" / "run_benchmark.py"),
        "--adapter", "external-command", "--agent-version", spec.version,
        "--external-command", json.dumps(config["external_command"]),
        "--adapter-capabilities", json.dumps(config["adapter_capabilities"]),
        "--external-observation-mode", spec.observation_mode,
        "--manifest", str(manifest), "--trials", "1", "--output-dir", str(runtime),
    ]
    if unsafe_trusted_host:
        command.append("--unsafe-trusted-host")
    completed = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
    _print_smoke(runtime, _smoke_result(runtime))
    if completed.returncode not in {0, 1}:
        diagnostic = completed.stderr.strip() or completed.stdout.strip()
        if diagnostic:
            print(f"Setup error: {diagnostic.splitlines()[-1]}", file=sys.stderr)
    if open_console and runtime.exists():
        return _serve_console(runtime, port)
    return completed.returncode


def _load_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _validate_experiment_specs(baseline_path: str, candidate_path: str):
    baseline, candidate = load_agent_spec(baseline_path), load_agent_spec(candidate_path)
    if baseline.agent_id != candidate.agent_id:
        raise AgentSpecError("baseline.agent.id and candidate.agent.id must be the same")
    if baseline.version == candidate.version:
        raise AgentSpecError("baseline.agent.version and candidate.agent.version must be different")
    if baseline.observation_mode != candidate.observation_mode:
        raise AgentSpecError("baseline and candidate observation.mode must match (blackbox vs sdk is not comparable)")
    if baseline.project_id is None or candidate.project_id is None:
        raise AgentSpecError("baseline and candidate project_id are required for a version Experiment")
    if baseline.project_id != candidate.project_id:
        raise AgentSpecError("baseline and candidate project_id must be the same")
    return baseline, candidate


def _gate_missing_evidence(gate: dict[str, object]) -> list[str]:
    labels = {
        "average_model_tokens_limit": "model token usage",
        "average_tool_calls_limit": "tool-call evidence",
    }
    return [labels[str(rule.get("name"))] for rule in gate.get("rules", [])
            if isinstance(rule, dict) and rule.get("actual") is None and str(rule.get("name")) in labels]


def _print_experiment(runtime: Path, report: dict[str, object] | None, gate: dict[str, object] | None,
                      project_id: str, agent_id: str, baseline_version: str, candidate_version: str) -> None:
    print("Experiment complete\n")
    print(f"Project: {project_id}")
    print(f"Agent: {agent_id}")
    print(f"Baseline: {baseline_version}")
    print(f"Candidate: {candidate_version}")
    summaries = report.get("summaries", {}) if isinstance(report, dict) else {}
    baseline = summaries.get("baseline", {}) if isinstance(summaries, dict) else {}
    candidate = summaries.get("candidate", {}) if isinstance(summaries, dict) else {}
    cases = report.get("experiment", []) if isinstance(report, dict) else []
    baseline_jobs = baseline.get("jobs", []) if isinstance(baseline, dict) else []
    candidate_jobs = candidate.get("jobs", []) if isinstance(candidate, dict) else []
    print(f"Cases: {len(cases) if isinstance(cases, list) else 0}")
    print(f"Trials: {len(baseline_jobs) + len(candidate_jobs)}\n")
    def passed(jobs: object) -> str:
        rows = jobs if isinstance(jobs, list) else []
        return f"{sum(bool(item.get('evaluation_passed')) for item in rows if isinstance(item, dict))}/{len(rows)}"
    print("Evaluation:")
    print(f"Baseline {passed(baseline_jobs)}")
    print(f"Candidate {passed(candidate_jobs)}\n")
    decision = gate.get("decision", {}) if isinstance(gate, dict) else {}
    status = str(decision.get("status", "not_available")).upper()
    print(f"Gate: {status}")
    missing = _gate_missing_evidence(gate or {})
    if missing:
        print("\nMissing release evidence:")
        for item in missing:
            print(f"- {item}")
    print(f"\nRuntime: {runtime}")
    print("View report:")
    print(f"regression-lab console --runtime {runtime}")


def _run_experiment(baseline_path: str, candidate_path: str, benchmarks: list[str], trials: int,
                    unsafe_trusted_host: bool, *, open_console: bool, port: int | None) -> int:
    try:
        baseline, candidate = _validate_experiment_specs(baseline_path, candidate_path)
    except AgentSpecError as exc:
        print("Experiment configuration failed:\n" + str(exc), file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    assert baseline.project_id is not None
    runtime = root / ".runtime" / "projects" / baseline.project_id / "experiments" / f"{baseline.agent_id}-{baseline.version}-vs-{candidate.version}-{stamp}"
    arm_configs = {
        "baseline": {**baseline.as_external_command_config(), "agent_spec_snapshot": baseline.snapshot()},
        "candidate": {**candidate.as_external_command_config(), "agent_spec_snapshot": candidate.snapshot()},
    }
    command = [
        sys.executable, str(root / "scripts" / "run_experiment.py"),
        "--adapter", "external-command", "--agents", f"baseline:{baseline.version},candidate:{candidate.version}",
        "--external-arm-configs", json.dumps(arm_configs), "--trials", str(trials),
        "--output-dir", str(runtime), "--project-id", baseline.project_id,
    ]
    for benchmark in benchmarks:
        command.extend(["--manifest", str(Path(benchmark).resolve())])
    command.append("--unsafe-trusted-host" if unsafe_trusted_host else "--docker")
    completed = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
    report = _load_json(runtime / "experiment.json")
    gate = None
    if report is not None:
        try:
            policy = _load_json(root / "configs" / "default-gate.json") or {}
            gate = evaluate_gate(report, policy)
            write_json_atomically(runtime / "gate-report.json", gate)
        except ValueError as exc:
            print(f"Gate evaluation error: {exc}", file=sys.stderr)
    _print_experiment(runtime, report, gate, baseline.project_id, baseline.agent_id, baseline.version, candidate.version)
    if completed.returncode not in {0, 1}:
        diagnostic = completed.stderr.strip() or completed.stdout.strip()
        if diagnostic:
            print(f"Setup error: {diagnostic.splitlines()[-1]}", file=sys.stderr)
    if open_console and runtime.exists():
        return _serve_console(runtime, port)
    return completed.returncode


def _verify_experiment(runtime: str) -> int:
    report = verify_experiment_runtime(runtime)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="regression-lab")
    commands = parser.add_subparsers(dest="command", required=True)
    agent = commands.add_parser("agent", help="validate an AgentSpec before running a Trial")
    agent_commands = agent.add_subparsers(dest="agent_command", required=True)
    validate = agent_commands.add_parser("validate", help="perform static AgentSpec validation only")
    validate.add_argument("spec", help="path to agent.yaml")
    smoke = agent_commands.add_parser("smoke", help="run one external-command Trial through the existing Runner")
    smoke.add_argument("spec", help="path to agent.yaml")
    smoke.add_argument("--benchmark", help="optional Benchmark Manifest path; defaults to the built-in smoke Case")
    smoke.add_argument("--unsafe-trusted-host", action="store_true", help="run platform tests on this trusted host instead of Docker")
    smoke.add_argument("--open", action="store_true", help="open the read-only Console after the Trial completes")
    smoke.add_argument("--port", type=int, help="Console port used with --open")
    experiment = commands.add_parser("experiment", help="compare two AgentSpecs through the existing Experiment Runtime")
    experiment_commands = experiment.add_subparsers(dest="experiment_command", required=True)
    run = experiment_commands.add_parser("run", help="run a baseline/candidate AgentSpec experiment")
    run.add_argument("--baseline", required=True, help="baseline AgentSpec path")
    run.add_argument("--candidate", required=True, help="candidate AgentSpec path")
    run.add_argument("--benchmark", action="append", required=True, help="Benchmark Manifest path; repeat for multiple Cases")
    run.add_argument("--trials", type=int, default=3, help="Trials per Case and Agent")
    run.add_argument("--unsafe-trusted-host", action="store_true", help="run platform tests on this trusted host instead of Docker")
    run.add_argument("--open", action="store_true", help="open the read-only Console after the Experiment completes")
    run.add_argument("--port", type=int, help="Console port used with --open")
    verify = experiment_commands.add_parser(
        "verify", help="verify Protocol, schedule, selected Attempts, Traces, and Gate linkage"
    )
    verify.add_argument("--runtime", required=True, help="completed Experiment Runtime directory")
    console = commands.add_parser("console", help="serve a read-only Console for an existing runtime")
    console.add_argument("--runtime", required=True, help="Experiment or Trial runtime directory")
    console.add_argument("--port", type=int, help="Console port; defaults to the first free port from 8765")
    args = parser.parse_args()
    if args.command == "agent" and args.agent_command == "validate":
        return _validate_agent(args.spec)
    if args.command == "agent" and args.agent_command == "smoke":
        return _smoke_agent(args.spec, args.benchmark, args.unsafe_trusted_host, open_console=args.open, port=args.port)
    if args.command == "experiment" and args.experiment_command == "run":
        return _run_experiment(args.baseline, args.candidate, args.benchmark, args.trials, args.unsafe_trusted_host,
                               open_console=args.open, port=args.port)
    if args.command == "experiment" and args.experiment_command == "verify":
        return _verify_experiment(args.runtime)
    if args.command == "console":
        return _serve_console(Path(args.runtime).resolve(), args.port)
    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
