#!/usr/bin/env python3
"""Run Baseline/Candidate Benchmark versions and build a comparison report."""

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
from regression_lab.attribution import attribute_trial
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
    """Backfill diagnostics from immutable Trial artifacts for report-only rebuilds."""

    jobs = []
    for job in summary.get("jobs", []):
        if not isinstance(job, dict):
            continue
        enriched = dict(job)
        job_id = enriched.get("job_id")
        if isinstance(job_id, str):
            result_path = case_dir / job_id / "result.json"
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                result = None
            if isinstance(result, dict):
                # Source identity belongs to the selected immutable Attempt,
                # not to a potentially stale case summary projection.
                for field in (
                    "agent_source_hash",
                    "expected_agent_source_hash",
                    "agent_source_hash_matches_protocol",
                ):
                    if field in result:
                        enriched[field] = result[field]
                if not isinstance(enriched.get("behavior"), dict):
                    enriched["behavior"] = result.get("behavior") if isinstance(result.get("behavior"), dict) else summarize_trial_behavior(result)
                if not isinstance(enriched.get("failure_attribution"), dict):
                    enriched["failure_attribution"] = result.get("failure_attribution") if isinstance(result.get("failure_attribution"), dict) else attribute_trial(result)
                scores = {item.get("evaluator"): item for item in result.get("scores", []) if isinstance(item, dict)}
                enriched.setdefault("path_policy_passed", scores.get("path_policy", {}).get("passed"))
                diff_violations = (scores.get("diff", {}).get("actual", {}) or {}).get("violations", [])
                enriched.setdefault("diff_policy_violated", any(isinstance(violation, str) and violation != "empty_diff" for violation in diff_violations))
        jobs.append(enriched)
    return {**summary, "jobs": jobs}


def parse_agents(value: str) -> list[dict[str, str]]:
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


def build_comparison_arms(agents: list[dict[str, str]], summaries: dict[str, dict]) -> dict[str, dict]:
    """Compare every candidate independently with the first Champion Agent."""

    champion_id = agents[0]["id"]
    return {
        candidate["id"]: {
            "baseline_id": champion_id,
            "candidate_id": candidate["id"],
            "comparison": compare_summaries(summaries[champion_id], summaries[candidate["id"]]),
        }
        for candidate in agents[1:]
    }


def pairwise_report(report: dict, arm: dict) -> dict:
    """Materialize one arm into the established two-version report contract."""

    baseline_id, candidate_id = arm["baseline_id"], arm["candidate_id"]
    comparison = arm["comparison"]
    return {
        **report,
        "agents": [item for item in report["agents"] if item["id"] in {baseline_id, candidate_id}],
        "baseline_id": baseline_id,
        "candidate_id": candidate_id,
        "comparison": comparison,
        "summaries": {label: report["summaries"][label] for label in (baseline_id, candidate_id)},
        **{key: comparison[key] for key in ("case_comparisons", "reliability", "efficiency", "behavior", "failure_attribution", "statistics")},
    }


def _attempt_source_comparability(protocol: dict, agents: list[dict[str, str]], summaries: dict[str, dict]) -> dict[str, object]:
    """Ensure selected external Attempt evidence matches frozen Agent bytes."""

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
    return {"level": "strict", "differences": []} if not mismatches else {
        "level": "not_comparable", "differences": ["attempt_agent_source_hash"], "mismatched_attempts": mismatches,
    }


def describe_prompt_profiles(command: list[str] | None, agents: list[dict[str, str]],
                             manifests: list[dict]) -> dict[str, dict[str, str]]:
    """Ask a trusted external Agent for hashes of its final rendered Prompts."""

    if not command:
        return {}
    request = {
        "versions": [item["version"] for item in agents],
        "test_commands": [str((manifest.get("fixture") or {}).get("test_command", "")) for manifest in manifests],
    }
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join(filter(None, [str(REGRESSION / "src"), str(REGRESSION), os.environ.get("PYTHONPATH", "")])),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
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
        descriptor = profiles.get(agent["version"])
        if not isinstance(descriptor, dict):
            continue
        profile_id, prompt_hash = descriptor.get("profile_id"), descriptor.get("rendered_prompt_set_hash")
        if isinstance(profile_id, str) and profile_id and isinstance(prompt_hash, str) and prompt_hash.startswith("sha256:"):
            validated[agent["version"]] = {"profile_id": profile_id, "rendered_prompt_set_hash": prompt_hash}
    return validated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", action="append", required=True,
                        help="benchmark manifest; repeat for multiple Cases")
    parser.add_argument("--output-dir", default=str(REGRESSION / ".runtime" / "experiment"))
    parser.add_argument("--agents", default="baseline:react-agent-v1,candidate:react-agent-v2")
    parser.add_argument("--adapter", default="react-agent", help="registered Agent adapter ID")
    parser.add_argument("--external-command", help="JSON argv array used only with external-command")
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
    use_docker = not args.unsafe_trusted_host
    if args.bash and not use_docker:
        parser.error("--bash requires Docker; --unsafe-trusted-host is incompatible")
    try:
        agents = parse_agents(args.agents)
    except ValueError as exc:
        parser.error(str(exc))
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
        parser.error("--adapter external-command requires --external-command")
    if args.adapter != "external-command" and external_command:
        parser.error("--external-command is only valid with --adapter external-command")

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
        print(json.dumps({"experiment": [manifest["id"] for _, manifest in manifests], "jobs": expanded}, ensure_ascii=False, indent=2))
        return 0

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_profiles = (
        {} if args.report_only else
        describe_prompt_profiles(external_command, agents, [manifest for _, manifest in manifests])
    )
    if not args.report_only and args.adapter == "external-command" and len(prompt_profiles) != len(agents):
        print("PROTOCOL ERROR: external Agent must support --describe-protocol for every compared version.", file=sys.stderr)
        return 2
    protocol = build_protocol(
        manifests=[manifest for _, manifest in manifests], agents=agents, adapter=args.adapter,
        external_command=external_command, trials=args.trials or max(int(item["trial_index"]) for item in jobs),
        use_docker=use_docker, bash=args.bash, schedule_seed=args.schedule_seed,
        comparison_intent=args.comparison_intent,
        allowed_differences=args.allowed_differences or ["agents[].prompt_profile"],
        prompt_profiles=prompt_profiles,
    )
    protocol_path = output_dir / "protocol.json"
    protocol_comparability = {"level": "strict", "differences": []}
    if args.report_only and not protocol_path.exists():
        # Historical artifacts predate protocol freezing. A report-only read
        # must not invent a snapshot and make that old evidence look strict.
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
            # A readable frozen protocol is shared by every Trial in this
            # output directory.  Rebuilding derived reports must preserve
            # that strict evidence instead of depending on a mutable prior
            # experiment.json field.
            protocol_comparability = {"level": "strict", "differences": []}
        elif protocol_comparability["level"] != "strict" and not args.allow_protocol_mismatch:
            print("PROTOCOL MISMATCH: refusing to resume a non-comparable experiment; use a new output directory.", file=sys.stderr)
            return 2
        if not args.report_only and protocol_comparability["level"] != "strict":
            revision = output_dir / f"protocol-{protocol['protocol_fingerprint'].removeprefix('sha256:')[:16]}.json"
            write_json_atomically(revision, protocol)
        else:
            protocol = persisted_protocol
    elif not args.report_only:
        write_json_atomically(protocol_path, protocol)
    execution_plan = build_execution_plan(jobs, agents, seed=args.schedule_seed)
    if not args.report_only:
        plan_path = output_dir / "execution-plan.json"
        if plan_path.exists():
            try:
                previous_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous_plan = None
            if previous_plan != execution_plan:
                print("EXECUTION PLAN MISMATCH: refusing to change the persisted paired schedule.", file=sys.stderr)
                return 2
        else:
            write_json_atomically(plan_path, execution_plan)
    summaries: dict[str, dict] = {}
    manifests_by_id = {manifest["id"]: (path, manifest) for path, manifest in manifests}
    if not args.report_only:
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
            completed = subprocess.run(command, cwd=REGRESSION, text=True, capture_output=True, check=False)
            if completed.stdout:
                print(completed.stdout)
            if completed.stderr:
                print(completed.stderr, file=sys.stderr)
            if completed.returncode not in {0, 1}:
                return completed.returncode

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
        protocol_comparability = source_comparability

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
    report["case_comparisons"] = report["comparison"]["case_comparisons"]
    report["reliability"] = report["comparison"]["reliability"]
    report["efficiency"] = report["comparison"]["efficiency"]
    report["behavior"] = report["comparison"]["behavior"]
    report["failure_attribution"] = report["comparison"]["failure_attribution"]
    report["statistics"] = report["comparison"]["statistics"]
    write_json_atomically(output_dir / "experiment.json", report)
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
    # Report-only rebuilds are a read of persisted evidence. A historical
    # failed Trial is report data, not a failure to rebuild that report.
    if args.report_only:
        return 0
    return 0 if all(
        job.get("status") == "completed" and job.get("evaluation_passed")
        for summary in summaries.values() for job in summary.get("jobs", [])
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
