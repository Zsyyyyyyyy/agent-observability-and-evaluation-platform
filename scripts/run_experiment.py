#!/usr/bin/env python3
"""Run Baseline/Candidate Benchmark versions and build a comparison report."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_lab.experiment import compare_summaries, expand_experiment
from regression_lab.manifest import (
    ManifestError,
    expand_trials,
    load_manifest,
    safe_child_path,
    validate_identifier,
    validate_manifest,
)


REGRESSION = Path(__file__).resolve().parents[1]


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", action="append", required=True,
                        help="benchmark manifest; repeat for multiple Cases")
    parser.add_argument("--output-dir", default=str(REGRESSION / ".runtime" / "experiment"))
    parser.add_argument("--agents", default="baseline:react-agent-v1,candidate:react-agent-v2")
    parser.add_argument("--adapter", default="react-agent", help="registered Agent adapter ID")
    parser.add_argument("--s20-source", help="path to external s20 source when using s20-replay")
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
    args = parser.parse_args()
    use_docker = not args.unsafe_trusted_host
    if args.bash and not use_docker:
        parser.error("--bash requires Docker; --unsafe-trusted-host is incompatible")
    try:
        agents = parse_agents(args.agents)
    except ValueError as exc:
        parser.error(str(exc))

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
    summaries: dict[str, dict] = {}
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
            if args.report_only:
                if not summary_path.exists():
                    print(f"MISSING CASE SUMMARY: {summary_path}", file=sys.stderr)
                    return 2
                case_summary = json.loads(summary_path.read_text(encoding="utf-8"))
                agent_jobs.extend(case_summary.get("jobs", []))
                continue
            command = [
                sys.executable,
                str(REGRESSION / "scripts" / "run_benchmark.py"),
                "--manifest", str(manifest_path),
                "--output-dir", str(case_dir),
                "--adapter", args.adapter,
                "--agent-version", agent["version"],
            ]
            if args.trials:
                command.extend(["--trials", str(args.trials)])
            if args.s20_source:
                command.extend(["--s20-source", args.s20_source])
            if use_docker:
                command.append("--docker")
            else:
                command.append("--unsafe-trusted-host")
            if args.bash:
                command.append("--bash")
            if args.resume:
                command.append("--resume")
            completed = subprocess.run(command, cwd=REGRESSION, text=True, capture_output=True, check=False)
            if completed.stdout:
                print(completed.stdout)
            if completed.stderr:
                print(completed.stderr, file=sys.stderr)
            if not summary_path.exists():
                return completed.returncode or 1
            case_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            agent_jobs.extend(case_summary.get("jobs", []))
        summaries[agent["id"]] = {
            "manifest_ids": [manifest["id"] for _, manifest in manifests],
            "job_count": len(agent_jobs),
            "jobs": agent_jobs,
        }

    baseline_id, candidate_id = agents[0]["id"], agents[1]["id"]
    report = {
        "experiment": [manifest["id"] for _, manifest in manifests],
        "agents": agents,
        "baseline_id": baseline_id,
        "candidate_id": candidate_id,
        "comparison": compare_summaries(summaries[baseline_id], summaries[candidate_id]),
        "summaries": summaries,
    }
    (output_dir / "experiment.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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
