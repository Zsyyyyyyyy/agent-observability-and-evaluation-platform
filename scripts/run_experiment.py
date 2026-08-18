#!/usr/bin/env python3
"""运行多个 Agent 版本的基准实验，并生成可追溯的比较报告。

脚本的主流程可以概括为：读取 Case 清单 -> 固化实验协议和执行顺序 ->
逐个运行 Trial -> 汇总各版本结果 -> 比较 Champion 与候选版本 -> 写入报告。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_lab.experiment import compare_summaries, expand_experiment
from regression_lab.evolution_catalog import EvolutionCatalog
from regression_lab.behavior import summarize_trial_behavior
from regression_lab.behavior_diff import snapshot_trial_behavior
from regression_lab.attribution import attribute_trial
from regression_lab.adapters import AdapterCapabilities
from regression_lab.manifest import (
    ManifestError,
    expand_trials,
    load_manifest,
    safe_child_path,
    validate_identifier,
    validate_manifest,
)
from regression_lab.protocol import (
    DEFAULT_SCHEDULE_SEED,
    build_execution_plan,
    build_protocol,
    compare_protocols,
    write_json_atomically,
)


REGRESSION = Path(__file__).resolve().parents[1]


def _hydrate_trial_diagnostics(summary: dict, case_dir: Path) -> dict:
    """从不可变的 Trial 产物中补齐诊断字段，供仅重建报告时使用。

    ``summary.json`` 是汇总投影，历史版本中可能缺少部分诊断信息；这里回读
    每个 Job 的 ``result.json``，避免仅重建报告时丢失来源哈希、行为和失败归因。
    """

    jobs = []
    for job in summary.get("jobs", []):
        if not isinstance(job, dict):
            continue
        enriched = dict(job)
        job_id = enriched.get("job_id")
        if isinstance(job_id, str):
            result_path = case_dir / job_id / "result.json"
            # 诊断信息缺失时只跳过该 Trial，不能让一个损坏的产物阻断其他结果汇总。
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                result = None
            if isinstance(result, dict):
                # 来源身份应以选定的不可变 Attempt 为准，而不是可能过期的 Case 汇总投影。
                for field in (
                    "adapter_id",
                    "adapter_capabilities",
                    "agent_source_hash",
                    "expected_agent_source_hash",
                    "agent_source_hash_matches_protocol",
                ):
                    if field in result:
                        enriched[field] = result[field]
                if not isinstance(enriched.get("behavior"), dict):
                    enriched["behavior"] = result.get("behavior") if isinstance(result.get("behavior"), dict) else summarize_trial_behavior(result)
                if not isinstance(enriched.get("behavior_snapshot"), dict) or not isinstance(enriched["behavior_snapshot"].get("patterns"), dict):
                    enriched["behavior_snapshot"] = snapshot_trial_behavior(result)
                if not isinstance(enriched.get("failure_attribution"), dict):
                    enriched["failure_attribution"] = result.get("failure_attribution") if isinstance(result.get("failure_attribution"), dict) else attribute_trial(result)
                # 旧产物可能没有直接保存这两个派生字段，因此从评测分数中恢复。
                scores = {item.get("evaluator"): item for item in result.get("scores", []) if isinstance(item, dict)}
                enriched.setdefault("path_policy_passed", scores.get("path_policy", {}).get("passed"))
                diff_violations = (scores.get("diff", {}).get("actual", {}) or {}).get("violations", [])
                enriched.setdefault("diff_policy_violated", any(isinstance(violation, str) and violation != "empty_diff" for violation in diff_violations))
        jobs.append(enriched)
    return {**summary, "jobs": jobs}


def parse_agents(value: str) -> list[dict[str, str]]:
    """解析 ``id:version,id:version`` 格式的 Agent 列表。

    列表中的第一个 Agent 是 Champion（基线），后续 Agent 都会分别与它比较。
    """

    agents = []
    for item in value.split(","):
        agent_id, separator, version = item.partition(":")
        if not separator or not agent_id or not version:
            raise ValueError("agents must use id:version,id:version syntax")
        try:
            agents.append({"id": validate_identifier(agent_id, "agent id"), "version": version})
        except ManifestError as exc:
            raise ValueError(str(exc)) from exc
    if len(agents) < 2:
        raise ValueError("experiment requires at least two agents")
    return agents


def parse_external_arm_configs(value: str | None, agents: list[dict[str, str]]) -> dict[str, dict[str, object]] | None:
    """Validate the optional per-arm external-command inputs used by onboarding.

    This remains an execution bridge.  The immutable AgentSpec snapshot is
    stored separately in Protocol, rather than deriving identity from argv.
    """

    if value is None:
        return None
    try:
        raw = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--external-arm-configs must be a JSON object: {exc.msg}") from exc
    labels = {item["id"] for item in agents}
    if not isinstance(raw, dict) or set(raw) != labels:
        raise ValueError("--external-arm-configs must provide exactly one config for every Agent label")
    configs: dict[str, dict[str, object]] = {}
    for label in labels:
        config = raw.get(label)
        if not isinstance(config, dict):
            raise ValueError(f"--external-arm-configs.{label} must be an object")
        command = config.get("external_command")
        if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
            raise ValueError(f"--external-arm-configs.{label}.external_command must be a non-empty argv string array")
        capabilities = AdapterCapabilities.from_snapshot(config.get("adapter_capabilities"))
        if capabilities is None:
            raise ValueError(f"--external-arm-configs.{label}.adapter_capabilities must provide every AdapterCapabilities boolean field")
        mode = config.get("observation_mode")
        if mode not in {"sdk", "blackbox"}:
            raise ValueError(f"--external-arm-configs.{label}.observation_mode must be sdk or blackbox")
        snapshot = config.get("agent_spec_snapshot")
        if snapshot is not None and not isinstance(snapshot, dict):
            raise ValueError(f"--external-arm-configs.{label}.agent_spec_snapshot must be an object")
        configs[label] = {
            "external_command": command,
            "adapter_capabilities": capabilities.as_dict(),
            "observation_mode": mode,
            **({"agent_spec_snapshot": snapshot} if isinstance(snapshot, dict) else {}),
        }
    return configs


def build_comparison_arms(agents: list[dict[str, str]], summaries: dict[str, dict]) -> dict[str, dict]:
    """让每个候选版本分别与第一个 Champion Agent 建立比较臂。"""

    champion_id = agents[0]["id"]
    return {
        candidate["id"]: {
            "baseline_id": champion_id,
            "candidate_id": candidate["id"],
            "comparison": compare_summaries(
                summaries[champion_id], summaries[candidate["id"]],
                baseline_version=next(agent["version"] for agent in agents if agent["id"] == champion_id),
                candidate_version=candidate["version"],
            ),
        }
        for candidate in agents[1:]
    }


def pairwise_report(report: dict, arm: dict) -> dict:
    """把一个比较臂展开成既有的双版本报告格式。

    Evolution Catalog 以双版本实验为基本记录单位，因此多候选实验需要为每个
    候选版本生成一份成对报告再分别入库。
    """

    baseline_id, candidate_id = arm["baseline_id"], arm["candidate_id"]
    comparison = arm["comparison"]
    return {
        **report,
        "agents": [item for item in report["agents"] if item["id"] in {baseline_id, candidate_id}],
        "baseline_id": baseline_id,
        "candidate_id": candidate_id,
        "comparison": comparison,
        "summaries": {label: report["summaries"][label] for label in (baseline_id, candidate_id)},
        **{key: comparison[key] for key in ("case_comparisons", "reliability", "efficiency", "behavior", "behavior_diff", "failure_attribution", "statistics")},
    }


def _attempt_source_comparability(protocol: dict, agents: list[dict[str, str]], summaries: dict[str, dict]) -> dict[str, object]:
    """确认外部 Agent 的 Attempt 证据与冻结的 Agent 源码字节一致。"""

    # 协议记录的是每个版本预期的源码哈希；实际结果来自已选中的 Attempt。
    expected_by_label = {
        item.get("label"): item.get("agent_source_hash")
        for item in protocol.get("agents", [])
        if isinstance(item, dict) and isinstance(item.get("label"), str)
    }
    mismatches: list[str] = []
    for agent in agents:
        expected = expected_by_label.get(agent["id"])
        if not isinstance(expected, str):
            continue
        for job in summaries.get(agent["id"], {}).get("jobs", []):
            if not isinstance(job, dict):
                continue
            actual = job.get("agent_source_hash")
            if actual != expected:
                mismatches.append(f"{agent['id']}:{job.get('job_id', 'unknown')}")
    # 任一 Attempt 的来源不匹配，就不能把本次结果视为严格可比。
    return {"level": "strict", "differences": []} if not mismatches else {
        "level": "not_comparable", "differences": ["attempt_agent_source_hash"], "mismatched_attempts": mismatches,
    }


def describe_prompt_profiles(command: list[str] | None, agents: list[dict[str, str]],
                             manifests: list[dict]) -> dict[str, dict[str, str]]:
    """向受信任的外部 Agent 请求最终渲染 Prompt 的哈希。

    Prompt Profile 是协议可比性的一部分。外部 Agent 必须通过
    ``--describe-protocol`` 返回每个版本的 profile ID 和 Prompt 集合哈希。
    """

    if not command:
        return {}
    request = {
        "versions": [item["version"] for item in agents],
        "test_commands": [str((manifest.get("fixture") or {}).get("test_command", "")) for manifest in manifests],
    }
    # 子进程只继承执行所需的最小环境，并确保能导入当前项目的源码。
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join(filter(None, [str(REGRESSION / "src"), str(REGRESSION), os.environ.get("PYTHONPATH", "")])),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        # 该探测只用于读取协议元数据，超时或输出非法时返回空结果，由调用方拒绝实验。
        completed = subprocess.run(
            [*command, "--describe-protocol"], input=json.dumps(request), cwd=REGRESSION,
            env=environment, text=True, capture_output=True, timeout=10, check=False,
        )
        payload = json.loads(completed.stdout) if completed.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        payload = None
    profiles = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(profiles, dict):
        return {}
    validated: dict[str, dict[str, str]] = {}
    for agent in agents:
        # 只接受格式完整且使用 sha256 前缀的 Prompt 哈希，避免把不可信输出写入协议。
        descriptor = profiles.get(agent["version"])
        if not isinstance(descriptor, dict):
            continue
        profile_id, prompt_hash = descriptor.get("profile_id"), descriptor.get("rendered_prompt_set_hash")
        if isinstance(profile_id, str) and profile_id and isinstance(prompt_hash, str) and prompt_hash.startswith("sha256:"):
            validated[agent["version"]] = {"profile_id": profile_id, "rendered_prompt_set_hash": prompt_hash}
    return validated


def main() -> int:
    """解析命令行参数，执行实验，并返回适合命令行使用的退出码。"""

    parser = argparse.ArgumentParser()
    # 一个实验可以包含多个 benchmark manifest；每个 manifest 会展开成多个 Trial。
    parser.add_argument("--manifest", action="append", required=True,
                        help="benchmark manifest; repeat for multiple Cases")
    parser.add_argument("--output-dir", default=str(REGRESSION / ".runtime" / "experiment"))
    parser.add_argument("--agents", default="baseline:react-agent-v1,candidate:react-agent-v2")
    parser.add_argument("--adapter", default="react-agent", help="registered Agent adapter ID")
    parser.add_argument("--external-command", help="JSON argv array used only with external-command")
    parser.add_argument("--adapter-capabilities", help="Evidence Capability JSON snapshot for an external-command Agent")
    parser.add_argument("--external-arm-configs", help="platform-owned JSON configs keyed by Agent label")
    parser.add_argument("--replay-source", help="path to external external Agent source when using readonly-replay")
    parser.add_argument("--trials", type=int)
    parser.add_argument("--docker", action="store_true", help="use Docker Sandbox (default)")
    parser.add_argument("--unsafe-trusted-host", action="store_true")
    parser.add_argument("--bash", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="rebuild experiment.json from existing case summary artifacts without running Agents",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--schedule-seed", type=int, default=DEFAULT_SCHEDULE_SEED,
                        help="seed for the persisted paired, interleaved execution plan")
    parser.add_argument("--comparison-intent", default="prompt_profile_only",
                        help="frozen description of the intervention under test")
    parser.add_argument("--allowed-difference", action="append", dest="allowed_differences",
                        help="repeatable protocol field allowed to differ between versions")
    parser.add_argument("--allow-protocol-mismatch", action="store_true",
                        help="record a non-comparable protocol revision instead of refusing an unsafe resume")
    parser.add_argument(
        "--evolution-catalog",
        help="path to the Evolution Catalog; defaults to <output-dir>/../evolution-catalog.json",
    )
    args = parser.parse_args()

    # 默认使用 Docker Sandbox；只有显式指定 unsafe 选项才允许在宿主机直接执行。
    use_docker = not args.unsafe_trusted_host
    if args.bash and not use_docker:
        parser.error("--bash requires Docker; --unsafe-trusted-host is incompatible")
    try:
        agents = parse_agents(args.agents)
        external_arm_configs = parse_external_arm_configs(args.external_arm_configs, agents)
    except ValueError as exc:
        parser.error(str(exc))

    # external-command 的参数以 JSON argv 数组传入，避免 shell 字符串解析带来的歧义。
    external_command: list[str] | None = None
    if args.external_command:
        try:
            decoded = json.loads(args.external_command)
        except json.JSONDecodeError as exc:
            parser.error(f"--external-command must be a JSON argv array: {exc.msg}")
        if not isinstance(decoded, list) or not decoded or not all(isinstance(item, str) and item for item in decoded):
            parser.error("--external-command must be a non-empty JSON argv string array")
        external_command = decoded
    if args.adapter == "external-command" and not external_command:
        if external_arm_configs is None:
            parser.error("--adapter external-command requires --external-command")
    if args.adapter != "external-command" and external_command:
        parser.error("--external-command is only valid with --adapter external-command")
    if args.adapter != "external-command" and external_arm_configs is not None:
        parser.error("--external-arm-configs is only valid with --adapter external-command")
    if external_arm_configs is not None and external_command is not None:
        parser.error("--external-arm-configs cannot be combined with --external-command")
    adapter_capabilities: dict[str, object] | None = None
    if args.adapter_capabilities:
        if args.adapter != "external-command":
            parser.error("--adapter-capabilities is only valid with --adapter external-command")
        try:
            declared_capabilities = json.loads(args.adapter_capabilities)
        except json.JSONDecodeError as exc:
            parser.error(f"--adapter-capabilities must be a JSON object: {exc.msg}")
        parsed_capabilities = AdapterCapabilities.from_snapshot(declared_capabilities)
        if parsed_capabilities is None:
            parser.error("--adapter-capabilities must provide every AdapterCapabilities boolean field")
        adapter_capabilities = parsed_capabilities.as_dict()

    # 读取并校验所有 Case，再把 Case 展开成具体的 Job/Trial。
    manifest_paths = [Path(path).resolve() for path in args.manifest]
    manifests = []
    jobs = []
    for manifest_path in manifest_paths:
        manifest = load_manifest(manifest_path)
        validation = validate_manifest(manifest, REGRESSION)
        if not validation.valid:
            print(json.dumps(validation.as_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
            return 2
        manifests.append((manifest_path, manifest))
        jobs.extend(expand_trials(manifest, REGRESSION, args.trials))
    expanded = expand_experiment(jobs, agents)
    if args.dry_run:
        # dry-run 只展示展开后的实验，不创建目录、不调用 Agent。
        print(json.dumps({"experiment": [manifest["id"] for _, manifest in manifests], "jobs": expanded}, ensure_ascii=False, indent=2))
        return 0

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    # 报告重建不能再次调用外部 Agent；正常运行时则先冻结 Prompt Profile 元数据。
    prompt_profiles: dict[str, dict[str, str]] = {}
    if not args.report_only and external_arm_configs is not None:
        for agent in agents:
            config = external_arm_configs[agent["id"]]
            if config["observation_mode"] == "blackbox":
                continue
            prompt_profiles.update(describe_prompt_profiles(config["external_command"], [agent], [manifest for _, manifest in manifests]))
    elif not args.report_only:
        prompt_profiles = describe_prompt_profiles(external_command, agents, [manifest for _, manifest in manifests])
    sdk_agent_count = len(agents) if external_arm_configs is None else sum(
        config["observation_mode"] == "sdk" for config in external_arm_configs.values()
    )
    if not args.report_only and args.adapter == "external-command" and len(prompt_profiles) != sdk_agent_count:
        print("PROTOCOL ERROR: external Agent must support --describe-protocol for every compared version.", file=sys.stderr)
        return 2
    agent_snapshots = {
        label: config["agent_spec_snapshot"]
        for label, config in (external_arm_configs or {}).items()
        if isinstance(config.get("agent_spec_snapshot"), dict)
    }
    protocol = build_protocol(
        manifests=[manifest for _, manifest in manifests], agents=agents, adapter=args.adapter,
        external_command=external_command, trials=args.trials or max(int(item["trial_index"]) for item in jobs),
        use_docker=use_docker, bash=args.bash, schedule_seed=args.schedule_seed,
        comparison_intent=args.comparison_intent,
        allowed_differences=args.allowed_differences or ["agents[].prompt_profile"],
        prompt_profiles=prompt_profiles,
        adapter_capabilities=adapter_capabilities,
        agent_snapshots=agent_snapshots,
    )
    protocol_path = output_dir / "protocol.json"
    protocol_comparability = {"level": "strict", "differences": []}
    if args.report_only and not protocol_path.exists():
        # 历史产物可能早于协议冻结；报告重建不能凭空创建快照并把旧证据标成严格可比。
        protocol = {}
        protocol_comparability = {"level": "not_available", "differences": ["protocol.json"]}
    elif protocol_path.exists():
        try:
            persisted_protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"PROTOCOL ERROR: unreadable {protocol_path}: {exc}", file=sys.stderr)
            return 2
        if not isinstance(persisted_protocol, dict):
            print(f"PROTOCOL ERROR: {protocol_path} must contain an object", file=sys.stderr)
            return 2
        protocol_comparability = compare_protocols(persisted_protocol, protocol)
        if args.report_only:
            protocol = persisted_protocol
            # 同一输出目录中的 Trial 共用可读的冻结协议；重建派生报告时应以它为准，
            # 不能依赖可能被修改过的 experiment.json 字段。
            protocol_comparability = {"level": "strict", "differences": []}
        elif protocol_comparability["level"] != "strict" and not args.allow_protocol_mismatch:
            print("PROTOCOL MISMATCH: refusing to resume a non-comparable experiment; use a new output directory.", file=sys.stderr)
            return 2
        if not args.report_only and protocol_comparability["level"] != "strict":
            # 允许协议不一致时，保留带指纹的修订快照，而不是覆盖原协议。
            revision = output_dir / f"protocol-{protocol['protocol_fingerprint'].removeprefix('sha256:')[:16]}.json"
            write_json_atomically(revision, protocol)
        else:
            protocol = persisted_protocol
    elif not args.report_only:
        write_json_atomically(protocol_path, protocol)

    # 执行计划固定 Case、Agent、Trial 的交错顺序，保证成对比较不受运行顺序影响。
    execution_plan = build_execution_plan(jobs, agents, seed=args.schedule_seed)
    if not args.report_only:
        plan_path = output_dir / "execution-plan.json"
        if plan_path.exists():
            try:
                previous_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous_plan = None
            if previous_plan != execution_plan:
                # 已开始的实验不能悄悄改变配对顺序，否则会破坏可复现实验条件。
                print("EXECUTION PLAN MISMATCH: refusing to change the persisted paired schedule.", file=sys.stderr)
                return 2
        else:
            write_json_atomically(plan_path, execution_plan)
    summaries: dict[str, dict] = {}
    manifests_by_id = {manifest["id"]: (path, manifest) for path, manifest in manifests}
    if not args.report_only:
        # 按冻结计划逐个调用 run_benchmark.py；每个入口负责一个 Agent/Case/Trial。
        for entry in execution_plan["entries"]:
            agent = next(item for item in agents if item["id"] == entry["agent_label"])
            manifest_path, manifest = manifests_by_id[entry["case_id"]]
            agent_dir = safe_child_path(output_dir, agent["id"], "agent id")
            case_dir = safe_child_path(agent_dir, manifest["id"], "manifest id")
            command = [
                sys.executable, str(REGRESSION / "scripts" / "run_benchmark.py"),
                "--manifest", str(manifest_path), "--output-dir", str(case_dir),
                "--adapter", args.adapter, "--agent-version", agent["version"],
                "--trial-index", str(entry["trial_index"]),
                "--protocol-fingerprint", str(protocol["protocol_fingerprint"]),
                "--schedule-index", str(entry["schedule_index"]),
            ]
            if external_command:
                command.extend(["--external-command", json.dumps(external_command)])
                if adapter_capabilities:
                    command.extend(["--adapter-capabilities", json.dumps(adapter_capabilities)])
                expected_hash = next(
                    (item.get("agent_source_hash") for item in protocol.get("agents", [])
                     if item.get("label") == agent["id"]),
                    None,
                )
                if isinstance(expected_hash, str):
                    command.extend(["--expected-agent-source-hash", expected_hash])
            elif external_arm_configs is not None:
                config = external_arm_configs[agent["id"]]
                command.extend([
                    "--external-command", json.dumps(config["external_command"]),
                    "--adapter-capabilities", json.dumps(config["adapter_capabilities"]),
                    "--external-observation-mode", str(config["observation_mode"]),
                ])
                expected_hash = next(
                    (item.get("agent_source_hash") for item in protocol.get("agents", [])
                     if item.get("label") == agent["id"]),
                    None,
                )
                if isinstance(expected_hash, str):
                    command.extend(["--expected-agent-source-hash", expected_hash])
            if args.trials:
                command.extend(["--trials", str(args.trials)])
            if args.replay_source:
                command.extend(["--replay-source", args.replay_source])
            command.append("--docker" if use_docker else "--unsafe-trusted-host")
            if args.bash:
                command.append("--bash")
            if args.resume:
                command.append("--resume")
            # 保留子进程的标准输出，便于观察每个 Trial 的执行过程。
            completed = subprocess.run(command, cwd=REGRESSION, text=True, capture_output=True, check=False)
            if completed.stdout:
                print(completed.stdout)
            if completed.stderr:
                print(completed.stderr, file=sys.stderr)
            if completed.returncode not in {0, 1}:
                return completed.returncode

    # 从每个 Agent/Case 的 summary.json 汇总结果；report-only 模式从这里开始只读已有产物。
    for agent in agents:
        try:
            agent_dir = safe_child_path(output_dir, agent["id"], "agent id")
        except ManifestError as exc:
            print(f"OUTPUT PATH ERROR: {exc}", file=sys.stderr)
            return 2
        agent_jobs = []
        for manifest_path, manifest in manifests:
            try:
                case_dir = safe_child_path(agent_dir, manifest["id"], "manifest id")
            except ManifestError as exc:
                print(f"OUTPUT PATH ERROR: {exc}", file=sys.stderr)
                return 2
            summary_path = case_dir / "summary.json"
            if not summary_path.exists():
                print(f"MISSING CASE SUMMARY: {summary_path}", file=sys.stderr)
                return 2
            case_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            case_summary = _hydrate_trial_diagnostics(case_summary, case_dir)
            agent_jobs.extend(case_summary.get("jobs", []))
        summaries[agent["id"]] = {
            "manifest_ids": [manifest["id"] for _, manifest in manifests],
            "job_count": len(agent_jobs),
            "jobs": agent_jobs,
        }

    source_comparability = _attempt_source_comparability(protocol, agents, summaries)
    if source_comparability["level"] != "strict":
        # Attempt 来源校验优先级高于协议字段，失败时整体降级为不可严格比较。
        protocol_comparability = source_comparability

    # 第一个 Agent 是 Champion；多候选时每个候选都单独生成一个比较臂。
    baseline_id, candidate_id = agents[0]["id"], agents[1]["id"]
    comparison_arms = build_comparison_arms(agents, summaries)
    primary_arm_id = candidate_id
    primary_comparison = comparison_arms[primary_arm_id]["comparison"]
    report = {
        "metrics_version": 3,
        "trial_count_required_per_case": 3,
        "experiment": [manifest["id"] for _, manifest in manifests],
        "agents": agents,
        "baseline_id": baseline_id,
        "candidate_id": candidate_id,
        "comparison": primary_comparison,
        "comparison_arms": comparison_arms,
        "champion_id": baseline_id,
        "primary_comparison_id": primary_arm_id,
        "summaries": summaries,
        "protocol": {"fingerprint": protocol.get("protocol_fingerprint"), "comparability": protocol_comparability},
    }
    # 保留顶层兼容字段，方便旧消费者无需读取 comparison 嵌套对象。
    report["case_comparisons"] = report["comparison"]["case_comparisons"]
    report["reliability"] = report["comparison"]["reliability"]
    report["efficiency"] = report["comparison"]["efficiency"]
    report["behavior"] = report["comparison"]["behavior"]
    report["behavior_diff"] = report["comparison"]["behavior_diff"]
    report["failure_attribution"] = report["comparison"]["failure_attribution"]
    report["statistics"] = report["comparison"]["statistics"]
    write_json_atomically(output_dir / "experiment.json", report)

    # 每个比较臂作为独立实验索引到 Evolution Catalog，便于后续追踪版本演进。
    catalog_path = Path(args.evolution_catalog).resolve() if args.evolution_catalog else output_dir.parent / "evolution-catalog.json"
    try:
        catalog = EvolutionCatalog(catalog_path)
        experiment_ids = {
            arm_id: catalog.index_experiment(pairwise_report(report, arm), artifact_root=output_dir,
                                              manifests=[manifest for _, manifest in manifests])
            for arm_id, arm in comparison_arms.items()
        }
    except ValueError as exc:
        print(f"EVOLUTION CATALOG ERROR: {exc}", file=sys.stderr)
        return 2
    report["evolution_experiment_id"] = experiment_ids[primary_arm_id]
    report["evolution_experiment_ids"] = experiment_ids
    report["evolution_catalog"] = str(catalog_path)
    write_json_atomically(output_dir / "experiment.json", report)
    print(json.dumps(report["comparison"], ensure_ascii=False, indent=2))
    # report-only 是对既有证据的读取；历史上失败的 Trial 属于报告数据，
    # 不应被误判为“报告重建失败”。
    if args.report_only:
        return 0
    # 运行本身即使完成，也要在任一 Job 未通过评测时返回 1，供 CI 判断失败。
    return 0 if all(
        job.get("status") == "completed" and job.get("evaluation_passed")
        for summary in summaries.values() for job in summary.get("jobs", [])
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
