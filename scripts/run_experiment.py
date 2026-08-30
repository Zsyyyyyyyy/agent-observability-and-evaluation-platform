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
from regression_lab.paths import asset_root, python_import_root, runtime_root


REGRESSION = asset_root()
LANGGRAPH_CAPABILITIES = AdapterCapabilities(
    trace=True, hierarchical_trace=True, model_usage=True, tool_trace=True,
    tool_semantics=False, test_trace=False, context_trace=False,
    workflow_trace=True, mcp_trace=False,
).as_dict()


def _hydrate_job_diagnostics(job: dict, case_dir: Path) -> dict:
    """用选定 Attempt 的结果补齐单个 Job 汇总中的历史诊断字段。"""

    hydrated = dict(job)
    job_id = hydrated.get("job_id")
    if not isinstance(job_id, str):
        return hydrated
    try:
        result = json.loads((case_dir / job_id / "result.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 单个历史产物损坏不能阻断其他 Trial 的报告重建。
        return hydrated
    if not isinstance(result, dict):
        return hydrated

    # 来源身份以选定的不可变 Attempt 为准，不能信任可能过期的 Case 汇总投影。
    identity_fields = (
        "adapter_id",
        "adapter_capabilities",
        "agent_source_hash",
        "expected_agent_source_hash",
        "agent_source_hash_matches_protocol",
    )
    for field in identity_fields:
        if field in result:
            hydrated[field] = result[field]

    persisted_behavior = hydrated.get("behavior")
    # 旧 summary 只有布尔 availability，无法表达 Capability Contract 的三态证据。
    if (
        not isinstance(persisted_behavior, dict)
        or not isinstance(persisted_behavior.get("evidence_availability"), dict)
    ):
        hydrated["behavior"] = summarize_trial_behavior(result)
    behavior_snapshot = hydrated.get("behavior_snapshot")
    if (
        not isinstance(behavior_snapshot, dict)
        or not isinstance(behavior_snapshot.get("patterns"), dict)
    ):
        hydrated["behavior_snapshot"] = snapshot_trial_behavior(result)
    if not isinstance(hydrated.get("failure_attribution"), dict):
        saved_attribution = result.get("failure_attribution")
        hydrated["failure_attribution"] = (
            saved_attribution if isinstance(saved_attribution, dict) else attribute_trial(result)
        )

    # 早期结果没有直接保存策略派生字段，只能从评测分数恢复。
    scores = {
        item.get("evaluator"): item
        for item in result.get("scores", [])
        if isinstance(item, dict)
    }
    hydrated.setdefault("path_policy_passed", scores.get("path_policy", {}).get("passed"))
    diff_violations = (scores.get("diff", {}).get("actual", {}) or {}).get("violations", [])
    hydrated.setdefault(
        "diff_policy_violated",
        any(
            isinstance(violation, str) and violation != "empty_diff"
            for violation in diff_violations
        ),
    )
    return hydrated


def _hydrate_trial_diagnostics(summary: dict, case_dir: Path) -> dict:
    """从不可变 Trial 产物补齐诊断字段，供仅重建报告时使用。"""

    jobs = [
        _hydrate_job_diagnostics(job, case_dir)
        for job in summary.get("jobs", [])
        if isinstance(job, dict)
    ]
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
    """校验快速接入流程提供的逐比较臂 external-command 配置。

    这里仍只是执行桥接；不可变 AgentSpec 快照独立保存在 Protocol 中，不能从
    argv 反推 Agent 身份。
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
        if mode not in {"sdk", "blackbox", "langgraph"}:
            raise ValueError(f"--external-arm-configs.{label}.observation_mode must be sdk, blackbox, or langgraph")
        if mode == "langgraph" and capabilities.as_dict() != LANGGRAPH_CAPABILITIES:
            raise ValueError(f"--external-arm-configs.{label}.langgraph capabilities are platform-defined")
        snapshot = config.get("agent_spec_snapshot")
        if snapshot is not None and not isinstance(snapshot, dict):
            raise ValueError(f"--external-arm-configs.{label}.agent_spec_snapshot must be an object")
        source_root = config.get("agent_source_root")
        if source_root is not None:
            if not isinstance(source_root, str) or not Path(source_root).is_absolute() or not Path(source_root).is_dir():
                raise ValueError(f"--external-arm-configs.{label}.agent_source_root must be an existing absolute directory")
        configs[label] = {
            "external_command": command,
            "adapter_capabilities": capabilities.as_dict(),
            "observation_mode": mode,
            **({"agent_source_root": source_root} if isinstance(source_root, str) else {}),
            **({"agent_spec_snapshot": snapshot} if isinstance(snapshot, dict) else {}),
        }
    return configs


def build_comparison_arms(agents: list[dict[str, str]], summaries: dict[str, dict]) -> dict[str, dict]:
    """让每个候选版本分别与第一个 Champion Agent 建立比较臂。"""

    champion = agents[0]
    champion_id = champion["id"]
    champion_version = champion["version"]
    champion_summary = summaries[champion_id]
    comparison_arms: dict[str, dict] = {}
    for candidate in agents[1:]:
        candidate_id = candidate["id"]
        comparison_arms[candidate_id] = {
            "baseline_id": champion_id,
            "candidate_id": candidate_id,
            "comparison": compare_summaries(
                champion_summary,
                summaries[candidate_id],
                baseline_version=champion_version,
                candidate_version=candidate["version"],
            ),
        }
    return comparison_arms


def pairwise_report(report: dict, arm: dict) -> dict:
    """把一个比较臂展开成既有的双版本报告格式。

    Evolution Catalog 以双版本实验为基本记录单位，因此多候选实验需要为每个
    候选版本生成一份成对报告再分别入库。
    """

    baseline_id, candidate_id = arm["baseline_id"], arm["candidate_id"]
    comparison = arm["comparison"]
    pairwise = {
        **report,
        "agents": [item for item in report["agents"] if item["id"] in {baseline_id, candidate_id}],
        "baseline_id": baseline_id,
        "candidate_id": candidate_id,
        "comparison": comparison,
        "summaries": {label: report["summaries"][label] for label in (baseline_id, candidate_id)},
    }
    for field in (
        "case_comparisons",
        "reliability",
        "efficiency",
        "behavior",
        "behavior_diff",
        "failure_attribution",
        "statistics",
    ):
        pairwise[field] = comparison[field]
    return pairwise


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
    if not mismatches:
        return {"level": "strict", "differences": []}
    return {
        "level": "not_comparable", "differences": ["attempt_agent_source_hash"], "mismatched_attempts": mismatches,
    }


def describe_prompt_profiles(command: list[str] | None, agents: list[dict[str, str]],
                             manifests: list[dict], source_root: str | None = None) -> dict[str, dict[str, str]]:
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
        "PYTHONPATH": os.pathsep.join(filter(None, [source_root, str(python_import_root()), os.environ.get("PYTHONPATH", "")])),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        # 该探测只用于读取协议元数据，超时或输出非法时返回空结果，由调用方拒绝实验。
        completed = subprocess.run(
            [*(item.replace("{agent_source}", source_root or "{agent_source}") for item in command), "--describe-protocol"], input=json.dumps(request), cwd=REGRESSION,
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


def _run_execution_plan(
    execution_plan: dict,
    *,
    agents: list[dict[str, str]],
    manifests_by_id: dict[str, tuple[Path, dict]],
    output_dir: Path,
    protocol: dict,
    args: argparse.Namespace,
    external_command: list[str] | None,
    external_arm_configs: dict[str, dict[str, object]] | None,
    adapter_capabilities: dict[str, object] | None,
    use_docker: bool,
) -> int:
    """按冻结顺序执行所有 Trial，返回首个基础设施级失败码。"""

    agents_by_label = {agent["id"]: agent for agent in agents}
    expected_source_by_label = {
        item.get("label"): item.get("agent_source_hash")
        for item in protocol.get("agents", [])
        if isinstance(item, dict)
    }
    expected_environment_by_label = {
        item.get("label"): (item.get("runtime_environment") or {}).get("identity_hash")
        for item in protocol.get("agents", [])
        if isinstance(item, dict) and isinstance(item.get("runtime_environment"), dict)
    }
    uses_external_command = external_command is not None or external_arm_configs is not None
    # 只能消费已落盘的计划条目；Resume 不能依据当前输入重新推导或调整 Trial 顺序。
    for entry in execution_plan["entries"]:
        agent_label = entry["agent_label"]
        agent = agents_by_label[agent_label]
        manifest_path, manifest = manifests_by_id[entry["case_id"]]
        agent_dir = safe_child_path(output_dir, agent["id"], "agent id")
        case_dir = safe_child_path(agent_dir, manifest["id"], "manifest id")
        command = [
            sys.executable,
            "-m", "scripts.run_benchmark",
            "--manifest", str(manifest_path),
            "--output-dir", str(case_dir),
            "--adapter", args.adapter,
            "--agent-version", agent["version"],
            "--trial-index", str(entry["trial_index"]),
            "--protocol-fingerprint", str(protocol["protocol_fingerprint"]),
            "--schedule-index", str(entry["schedule_index"]),
        ]
        if external_command is not None:
            command.extend(["--external-command", json.dumps(external_command)])
            if adapter_capabilities:
                command.extend(["--adapter-capabilities", json.dumps(adapter_capabilities)])
            command.extend(["--external-observation-mode", args.external_observation_mode])
        elif external_arm_configs is not None:
            config = external_arm_configs[agent["id"]]
            command.extend([
                "--external-command", json.dumps(config["external_command"]),
                "--adapter-capabilities", json.dumps(config["adapter_capabilities"]),
                "--external-observation-mode", str(config["observation_mode"]),
            ])
            source_root = config.get("agent_source_root")
            if isinstance(source_root, str):
                command.extend(["--external-source-root", source_root])
        expected_source = expected_source_by_label.get(agent_label)
        if uses_external_command and isinstance(expected_source, str):
            command.extend(["--expected-agent-source-hash", expected_source])
        expected_environment = expected_environment_by_label.get(agent_label)
        if uses_external_command and isinstance(expected_environment, str):
            command.extend(["--expected-runtime-environment-hash", expected_environment])
        if args.trials:
            command.extend(["--trials", str(args.trials)])
        if args.replay_source:
            command.extend(["--replay-source", args.replay_source])
        command.append("--docker" if use_docker else "--unsafe-trusted-host")
        if args.bash:
            command.append("--bash")
        if args.resume:
            command.append("--resume")

        completed = subprocess.run(
            command, cwd=REGRESSION, text=True, capture_output=True, check=False
        )
        if completed.stdout:
            print(completed.stdout)
        if completed.stderr:
            print(completed.stderr, file=sys.stderr)
        if completed.returncode not in {0, 1}:
            return completed.returncode
    return 0


def _build_experiment_report(
    manifests: list[tuple[Path, dict]],
    agents: list[dict[str, str]],
    summaries: dict[str, dict],
    protocol: dict,
    protocol_comparability: dict[str, object],
    project_id: str | None,
) -> tuple[dict, dict[str, dict], str]:
    """生成主比较报告，同时保留旧消费者需要的顶层字段。"""

    baseline_id, candidate_id = agents[0]["id"], agents[1]["id"]
    comparison_arms = build_comparison_arms(agents, summaries)
    primary_comparison = comparison_arms[candidate_id]["comparison"]
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
        "primary_comparison_id": candidate_id,
        "summaries": summaries,
        "protocol": {
            "fingerprint": protocol.get("protocol_fingerprint"),
            "comparability": protocol_comparability,
        },
    }
    if project_id is not None:
        report["project_id"] = project_id
    for field in (
        "case_comparisons",
        "reliability",
        "efficiency",
        "behavior",
        "behavior_diff",
        "failure_attribution",
        "statistics",
    ):
        report[field] = primary_comparison[field]
    return report, comparison_arms, candidate_id


def _load_agent_summaries(
    output_dir: Path,
    agents: list[dict[str, str]],
    manifests: list[tuple[Path, dict]],
) -> dict[str, dict]:
    """读取各 Agent/Case 的汇总投影，并补齐历史诊断字段。"""

    summaries: dict[str, dict] = {}
    manifest_ids = [manifest["id"] for _, manifest in manifests]
    for agent in agents:
        agent_dir = safe_child_path(output_dir, agent["id"], "agent id")
        agent_jobs = []
        for _, manifest in manifests:
            case_dir = safe_child_path(agent_dir, manifest["id"], "manifest id")
            summary_path = case_dir / "summary.json"
            if not summary_path.exists():
                raise FileNotFoundError(summary_path)
            case_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            hydrated_summary = _hydrate_trial_diagnostics(case_summary, case_dir)
            agent_jobs.extend(hydrated_summary.get("jobs", []))
        summaries[agent["id"]] = {
            "manifest_ids": manifest_ids,
            "job_count": len(agent_jobs),
            "jobs": agent_jobs,
        }
    return summaries


def _index_evolution_report(
    report: dict,
    comparison_arms: dict[str, dict],
    *,
    primary_arm_id: str,
    output_dir: Path,
    catalog_argument: str | None,
    project_id: str | None,
    agents: list[dict[str, str]],
    manifests: list[tuple[Path, dict]],
) -> None:
    """把每个比较臂写入 Evolution Catalog，并补齐报告的演进身份。"""

    if catalog_argument:
        catalog_path = Path(catalog_argument).resolve()
    elif project_id is not None:
        catalog_path = runtime_root() / "projects" / project_id / "evolution-catalog.json"
    else:
        catalog_path = output_dir.parent / "evolution-catalog.json"

    catalog = EvolutionCatalog(catalog_path)
    manifest_documents = [manifest for _, manifest in manifests]
    experiment_ids: dict[str, str] = {}
    for arm_id, comparison_arm in comparison_arms.items():
        experiment_ids[arm_id] = catalog.index_experiment(
            pairwise_report(report, comparison_arm),
            artifact_root=output_dir,
            manifests=manifest_documents,
            project_id=project_id,
        )
    primary_experiment_id = experiment_ids[primary_arm_id]
    report["evolution_experiment_id"] = primary_experiment_id
    report["evolution_experiment_ids"] = experiment_ids
    report["evolution_catalog"] = str(catalog_path)

    catalog_document = catalog.load()
    indexed_experiment = next(
        (
            item for item in catalog_document["experiments"]
            if item.get("experiment_id") == primary_experiment_id
        ),
        {},
    )
    version_ids = {
        indexed_experiment.get("baseline_version_id"),
        indexed_experiment.get("candidate_version_id"),
    }
    agent_ids = {
        version.get("agent_id")
        for version in catalog_document["versions"]
        if version.get("version_id") in version_ids
        and isinstance(version.get("agent_id"), str)
    }
    report["evaluation_context"] = {
        "schema_version": 1,
        "project_id": project_id,
        "agent_id": agent_ids.pop() if len(agent_ids) == 1 else None,
        "experiment_id": primary_experiment_id,
        "baseline_version": agents[0]["version"],
        "candidate_version": agents[1]["version"],
        "legacy": False,
        "scope_status": "explicit",
        "source": "experiment_artifact",
    }


def _build_parser() -> argparse.ArgumentParser:
    """定义多版本 Experiment 的命令行参数。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", action="append", required=True,
                        help="benchmark manifest; repeat for multiple Cases")
    parser.add_argument("--output-dir", default=str(runtime_root() / "experiment"))
    parser.add_argument("--agents", default="baseline:react-agent-v1,candidate:react-agent-v2")
    parser.add_argument("--adapter", default="react-agent", help="registered Agent adapter ID")
    parser.add_argument("--external-command", help="JSON argv array used only with external-command")
    parser.add_argument("--adapter-capabilities", help="Evidence Capability JSON snapshot for an external-command Agent")
    parser.add_argument("--external-observation-mode", choices=("sdk", "blackbox", "langgraph"), default="sdk")
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
    parser.add_argument("--project-id", help="stable evaluation target identity used to isolate Evolution Catalogs")
    return parser


def _resolve_execution_config(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> tuple[
    list[dict[str, str]],
    dict[str, dict[str, object]] | None,
    list[str] | None,
    dict[str, object] | None,
]:
    """校验 Agent 和 Adapter 参数，并返回规范化执行配置。"""

    try:
        agents = parse_agents(args.agents)
        external_arm_configs = parse_external_arm_configs(args.external_arm_configs, agents)
        if args.project_id is not None:
            args.project_id = validate_identifier(args.project_id, "project_id")
    except ValueError as exc:
        parser.error(str(exc))

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
    if args.adapter == "external-command" and external_command is None and external_arm_configs is None:
        parser.error("--adapter external-command requires --external-command")
    if args.adapter != "external-command" and external_command:
        parser.error("--external-command is only valid with --adapter external-command")
    if args.adapter != "external-command" and external_arm_configs is not None:
        parser.error("--external-arm-configs is only valid with --adapter external-command")
    if external_arm_configs is not None and external_command is not None:
        parser.error("--external-arm-configs cannot be combined with --external-command")
    if external_arm_configs is not None and args.external_observation_mode != "sdk":
        parser.error("--external-observation-mode cannot be combined with --external-arm-configs")
    if args.adapter != "external-command" and args.external_observation_mode != "sdk":
        parser.error("--external-observation-mode is only valid with --adapter external-command")

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
    if args.external_observation_mode == "langgraph":
        if adapter_capabilities is not None and adapter_capabilities != LANGGRAPH_CAPABILITIES:
            parser.error("langgraph observation mode uses platform-defined capabilities")
        adapter_capabilities = LANGGRAPH_CAPABILITIES
    return agents, external_arm_configs, external_command, adapter_capabilities


def _load_and_expand_manifests(
    args: argparse.Namespace,
) -> tuple[list[tuple[Path, dict]], list[dict]] | None:
    """读取、校验 Case Manifest，并展开为具体 Trial。"""

    manifests: list[tuple[Path, dict]] = []
    jobs: list[dict] = []
    for path in args.manifest:
        manifest_path = Path(path).resolve()
        manifest = load_manifest(manifest_path)
        validation = validate_manifest(manifest, REGRESSION)
        if not validation.valid:
            print(json.dumps(validation.as_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
            return None
        manifests.append((manifest_path, manifest))
        jobs.extend(expand_trials(manifest, REGRESSION, args.trials))
    return manifests, jobs


def _freeze_or_restore_protocol(
    args: argparse.Namespace,
    *,
    agents: list[dict[str, str]],
    manifests: list[tuple[Path, dict]],
    jobs: list[dict],
    output_dir: Path,
    external_command: list[str] | None,
    external_arm_configs: dict[str, dict[str, object]] | None,
    adapter_capabilities: dict[str, object] | None,
    use_docker: bool,
) -> tuple[dict, dict[str, object]] | None:
    """冻结新 Protocol，或从已有 Artifact 恢复并校验 Protocol。"""

    manifest_documents = [manifest for _, manifest in manifests]
    prompt_profiles: dict[str, dict[str, str]] = {}
    if not args.report_only and external_arm_configs is not None:
        for agent in agents:
            arm_config = external_arm_configs[agent["id"]]
            if arm_config["observation_mode"] != "sdk":
                continue
            prompt_profiles.update(
                describe_prompt_profiles(
                    arm_config["external_command"],
                    [agent],
                    manifest_documents,
                    arm_config.get("agent_source_root") if isinstance(arm_config.get("agent_source_root"), str) else None,
                )
            )
    elif not args.report_only and args.external_observation_mode == "sdk":
        prompt_profiles = describe_prompt_profiles(external_command, agents, manifest_documents)

    prompt_profile_agent_count = len(agents) if args.external_observation_mode == "sdk" else 0
    if external_arm_configs is not None:
        prompt_profile_agent_count = sum(
            config["observation_mode"] == "sdk"
            for config in external_arm_configs.values()
        )
    if (
        not args.report_only
        and args.adapter == "external-command"
        and len(prompt_profiles) != prompt_profile_agent_count
    ):
        print(
            "PROTOCOL ERROR: external Agent must support --describe-protocol for every compared version.",
            file=sys.stderr,
        )
        return None

    agent_snapshots = {
        label: config["agent_spec_snapshot"]
        for label, config in (external_arm_configs or {}).items()
        if isinstance(config.get("agent_spec_snapshot"), dict)
    }
    protocol = build_protocol(
        manifests=manifest_documents,
        agents=agents,
        adapter=args.adapter,
        external_command=external_command,
        trials=args.trials or max(int(job["trial_index"]) for job in jobs),
        use_docker=use_docker,
        bash=args.bash,
        schedule_seed=args.schedule_seed,
        comparison_intent=args.comparison_intent,
        allowed_differences=args.allowed_differences or ["agents[].prompt_profile"],
        prompt_profiles=prompt_profiles,
        adapter_capabilities=adapter_capabilities,
        agent_snapshots=agent_snapshots,
    )

    protocol_path = output_dir / "protocol.json"
    protocol_comparability: dict[str, object] = {"level": "strict", "differences": []}
    if args.report_only and not protocol_path.exists():
        # 历史产物可能早于协议冻结；报告重建不能凭空创建快照并把旧证据标成严格可比。
        return {}, {"level": "not_available", "differences": ["protocol.json"]}
    if not protocol_path.exists():
        if not args.report_only:
            write_json_atomically(protocol_path, protocol)
        return protocol, protocol_comparability

    try:
        persisted_protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"PROTOCOL ERROR: unreadable {protocol_path}: {exc}", file=sys.stderr)
        return None
    if not isinstance(persisted_protocol, dict):
        print(f"PROTOCOL ERROR: {protocol_path} must contain an object", file=sys.stderr)
        return None

    protocol_comparability = compare_protocols(persisted_protocol, protocol)
    if args.report_only:
        # 同一输出目录中的 Trial 共用可读的冻结协议；重建派生报告时应以它为准，
        # 不能依赖可能被修改过的 experiment.json 字段。
        return persisted_protocol, {"level": "strict", "differences": []}
    if protocol_comparability["level"] != "strict" and not args.allow_protocol_mismatch:
        print(
            "PROTOCOL MISMATCH: refusing to resume a non-comparable experiment; use a new output directory.",
            file=sys.stderr,
        )
        return None
    if protocol_comparability["level"] != "strict":
        # 允许协议不一致时，保留带指纹的修订快照，而不是覆盖原协议。
        revision = output_dir / f"protocol-{protocol['protocol_fingerprint'].removeprefix('sha256:')[:16]}.json"
        write_json_atomically(revision, protocol)
        return protocol, protocol_comparability
    return persisted_protocol, protocol_comparability


def _build_and_persist_execution_plan(
    args: argparse.Namespace,
    jobs: list[dict],
    agents: list[dict[str, str]],
    output_dir: Path,
) -> dict | None:
    """构建配对执行计划，并拒绝改写已冻结的计划。"""

    execution_plan = build_execution_plan(jobs, agents, seed=args.schedule_seed)
    if args.report_only:
        return execution_plan

    plan_path = output_dir / "execution-plan.json"
    if not plan_path.exists():
        write_json_atomically(plan_path, execution_plan)
        return execution_plan
    try:
        previous_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous_plan = None
    if previous_plan != execution_plan:
        # 已开始的实验不能悄悄改变配对顺序，否则会破坏可复现实验条件。
        print(
            "EXECUTION PLAN MISMATCH: refusing to change the persisted paired schedule.",
            file=sys.stderr,
        )
        return None
    return execution_plan


def main() -> int:
    """解析命令行参数，执行实验，并返回适合命令行使用的退出码。"""

    parser = _build_parser()
    args = parser.parse_args()

    # 默认使用 Docker Sandbox；只有显式指定 unsafe 选项才允许在宿主机直接执行。
    use_docker = not args.unsafe_trusted_host
    if args.bash and not use_docker:
        parser.error("--bash requires Docker; --unsafe-trusted-host is incompatible")
    agents, external_arm_configs, external_command, adapter_capabilities = (
        _resolve_execution_config(parser, args)
    )

    # 加载实验输入。
    experiment_input = _load_and_expand_manifests(args)
    if experiment_input is None:
        return 2
    manifests, jobs = experiment_input

    expanded = expand_experiment(jobs, agents)
    if args.dry_run:
        # dry-run 只展示展开后的实验，不创建目录、不调用 Agent。
        print(json.dumps({"experiment": [manifest["id"] for _, manifest in manifests], "jobs": expanded}, ensure_ascii=False, indent=2))
        return 0

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # 冻结或恢复实验协议。
    protocol_state = _freeze_or_restore_protocol(
        args,
        agents=agents,
        manifests=manifests,
        jobs=jobs,
        output_dir=output_dir,
        external_command=external_command,
        external_arm_configs=external_arm_configs,
        adapter_capabilities=adapter_capabilities,
        use_docker=use_docker,
    )
    if protocol_state is None:
        return 2
    protocol, protocol_comparability = protocol_state

    # 构建并验证执行计划。
    execution_plan = _build_and_persist_execution_plan(args, jobs, agents, output_dir)
    if execution_plan is None:
        return 2

    manifests_by_id = {manifest["id"]: (path, manifest) for path, manifest in manifests}
    if not args.report_only:
        returncode = _run_execution_plan(
            execution_plan,
            agents=agents,
            manifests_by_id=manifests_by_id,
            output_dir=output_dir,
            protocol=protocol,
            args=args,
            external_command=external_command,
            external_arm_configs=external_arm_configs,
            adapter_capabilities=adapter_capabilities,
            use_docker=use_docker,
        )
        if returncode:
            return returncode

    # 从不可变证据生成报告；report-only 模式从这里开始不再调用 Agent。
    try:
        summaries = _load_agent_summaries(output_dir, agents, manifests)
    except ManifestError as exc:
        print(f"OUTPUT PATH ERROR: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"MISSING CASE SUMMARY: {exc.args[0]}", file=sys.stderr)
        return 2

    source_comparability = _attempt_source_comparability(protocol, agents, summaries)
    if source_comparability["level"] != "strict":
        # Attempt 来源校验优先级高于协议字段，失败时整体降级为不可严格比较。
        protocol_comparability = source_comparability

    report, comparison_arms, primary_arm_id = _build_experiment_report(
        manifests,
        agents,
        summaries,
        protocol,
        protocol_comparability,
        args.project_id,
    )
    write_json_atomically(output_dir / "experiment.json", report)

    # 写入演进目录。
    try:
        _index_evolution_report(
            report,
            comparison_arms,
            primary_arm_id=primary_arm_id,
            output_dir=output_dir,
            catalog_argument=args.evolution_catalog,
            project_id=args.project_id,
            agents=agents,
            manifests=manifests,
        )
    except ValueError as exc:
        print(f"EVOLUTION CATALOG ERROR: {exc}", file=sys.stderr)
        return 2
    write_json_atomically(output_dir / "experiment.json", report)
    print(json.dumps(report["comparison"], ensure_ascii=False, indent=2))

    # 返回 CI 状态。
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
