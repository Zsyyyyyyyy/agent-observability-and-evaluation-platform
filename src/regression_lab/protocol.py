"""Immutable, non-secret experiment protocol snapshots.

An experiment report answers *what happened*.  A protocol snapshot answers
*under which inputs was that comparison allowed to mean anything*.  Runtime
artifacts remain the source of evidence; this module only records stable,
non-sensitive identities of their inputs.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from regression_lab.artifacts import write_json_atomically


PROTOCOL_SCHEMA_VERSION = 2
DEFAULT_SCHEDULE_SEED = 20260813
_SENSITIVE_KEY = re.compile(r"(?:api[_.-]?key|authorization|secret|token|password)", re.IGNORECASE)


def canonical_json(value: Any) -> str:
    """Serialize a value deterministically for content-addressed identities."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_hash(path: str | Path) -> str:
    """Return a content hash, never an absolute path or file contents."""

    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_hash(path: str | Path) -> str:
    """Hash a fixture tree including relative filenames and bytes."""

    root = Path(path).resolve()
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest.update(item.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _module_entrypoint(interpreter: str, module: str, source_root: Path | None = None) -> Path | None:
    """定位 ``python -m`` 的本地模块文件，而不执行 Agent 本身。"""

    lookup = (
        "import importlib.util, sys; "
        "spec = importlib.util.find_spec(sys.argv[1]); "
        "print(spec.origin if spec and spec.origin else '')"
    )
    try:
        environment = os.environ.copy()
        if source_root is not None:
            environment["PYTHONPATH"] = os.pathsep.join(filter(None, [str(source_root), environment.get("PYTHONPATH", "")]))
        result = subprocess.run(
            [interpreter, "-c", lookup, module], env=environment,
            capture_output=True, text=True, check=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    location = result.stdout.strip()
    candidate = Path(location)
    return candidate.resolve() if location and candidate.is_file() else None


def agent_source_snapshot(command: Iterable[str], source_root: str | Path | None = None) -> dict[str, object]:
    """Identify the executable Agent source without persisting its local path."""

    root_hint = Path(source_root).resolve() if source_root else None
    arguments = [argument.replace("{agent_source}", str(root_hint)) if root_hint is not None else argument for argument in command]
    if len(arguments) >= 3 and arguments[1] == "-m":
        entrypoint = _module_entrypoint(arguments[0], arguments[2], root_hint)
    else:
        entrypoint = next((Path(value).resolve() for value in reversed(arguments) if Path(value).is_file()), None)
    if entrypoint is None:
        return {"agent_source_hash": None, "entrypoint_hash": None, "source_scope": "unavailable", "source_revision": None, "source_dirty": None}
    entrypoint_hash = file_hash(entrypoint)
    try:
        source_directory = root_hint if root_hint is not None else entrypoint.parent
        root_result = subprocess.run(
            ["git", "-C", str(source_directory), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True, timeout=3,
        )
        root = Path(root_result.stdout.strip())
        files_result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, check=True, timeout=3,
        )
        files = sorted({line for line in files_result.stdout.splitlines() if line})
        digest = hashlib.sha256()
        for relative in files:
            source = root / relative
            if not source.is_file():
                continue
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(source.read_bytes())
            digest.update(b"\0")
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=3,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1"],
            capture_output=True, text=True, check=True, timeout=3,
        ).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return {
            "agent_source_hash": entrypoint_hash,
            "entrypoint_hash": entrypoint_hash,
            "source_scope": "entrypoint_only",
            "source_revision": None,
            "source_dirty": None,
        }
    return {
        "agent_source_hash": "sha256:" + digest.hexdigest(),
        "entrypoint_hash": entrypoint_hash,
        "source_scope": "git_worktree",
        "source_revision": revision,
        "source_dirty": dirty,
    }


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _SENSITIVE_KEY.search(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


def protocol_fingerprint(protocol: dict[str, Any]) -> str:
    """Fingerprint a protocol excluding its own derived fingerprint field."""

    payload = dict(protocol)
    payload.pop("protocol_fingerprint", None)
    return sha256_text(canonical_json(payload))


def _manifest_snapshot(manifest: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(manifest["_manifest_path"])).resolve()
    fixture = manifest.get("fixture") if isinstance(manifest.get("fixture"), dict) else {}
    fixture_path = path.parents[1] / str(fixture.get("path", ""))
    normalized = {key: value for key, value in manifest.items() if key != "_manifest_path"}
    return {
        "case_id": manifest.get("id"),
        "manifest_hash": sha256_text(canonical_json(normalized)),
        "fixture_tree_hash": tree_hash(fixture_path),
        "test_command_hash": sha256_text(str(fixture.get("test_command", ""))),
        "policy_hash": sha256_text(canonical_json(manifest.get("tool_policy", {}))),
        "budget": {
            "max_tokens": (manifest.get("execution") or {}).get("max_tokens"),
            "max_tool_calls": (manifest.get("execution") or {}).get("max_tool_calls"),
            "timeout_seconds": (manifest.get("execution") or {}).get("timeout_seconds"),
        },
    }


def _source_hashes(adapter: str, external_command: list[str] | None) -> dict[str, str]:
    package_root = Path(__file__).resolve().parent
    sources = [package_root / "evaluators.py", package_root / "schema.py"]
    hashes = {source.name: file_hash(source) for source in sources if source.is_file()}
    hashes["regression_lab_tree"] = tree_hash(package_root)
    if adapter == "external-command" and external_command:
        identity = agent_source_snapshot(external_command)
        if isinstance(identity["agent_source_hash"], str):
            hashes["external_agent"] = identity["agent_source_hash"]
    return hashes


def _optional_float(name: str, default: float, *, maximum: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not 0.0 <= value <= maximum:
        raise ValueError(f"{name} must be between 0 and {maximum:g}")
    return value


def _seed_snapshot() -> int | str:
    """Return an explicit seed value or an explicit unsupported marker."""

    raw = os.environ.get("AGENT_SEED")
    if raw is None or not raw.strip():
        return "not_configured"
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError("AGENT_SEED must be an integer") from exc


def build_protocol(*, manifests: Iterable[dict[str, Any]], agents: list[dict[str, str]], adapter: str,
                   external_command: list[str] | None, trials: int, use_docker: bool, bash: bool,
                   schedule_seed: int = DEFAULT_SCHEDULE_SEED,
                   comparison_intent: str = "prompt_profile_only",
                   allowed_differences: Iterable[str] = ("agents[].prompt_profile",),
                   prompt_profiles: dict[str, dict[str, str]] | None = None,
                   adapter_capabilities: dict[str, object] | None = None,
                   agent_snapshots: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build a protocol snapshot from explicit experiment inputs only.

    Environment collection is deliberately narrow: model identity is useful,
    while credentials and arbitrary environment variables are not protocol
    inputs and must never be persisted.
    """

    snapshots = [_manifest_snapshot(manifest) for manifest in manifests]
    source_hashes = _source_hashes(adapter, external_command)
    profile_source_hash = agent_source_snapshot(external_command)["agent_source_hash"] if external_command else None
    prompt_profiles = prompt_profiles or {}
    agent_snapshots = agent_snapshots or {}
    protocol_agents = []
    for item in agents:
        descriptor = prompt_profiles.get(item["version"], {})
        snapshot = agent_snapshots.get(item["id"])
        source_hash = snapshot.get("agent_source_hash") if isinstance(snapshot, dict) else None
        entrypoint_hash = snapshot.get("entrypoint_hash") if isinstance(snapshot, dict) else None
        protocol_agents.append({
            "label": item["id"], "version": item["version"], "adapter": adapter,
            "agent_source_hash": source_hash if isinstance(source_hash, str) else entrypoint_hash if isinstance(entrypoint_hash, str) else profile_source_hash,
            "prompt_profile": descriptor.get("profile_id", item["version"]),
            "prompt_profile_source_hash": source_hash if isinstance(source_hash, str) else entrypoint_hash if isinstance(entrypoint_hash, str) else profile_source_hash,
            # The Agent protocol-description handshake hashes the final system
            # prompts rendered for every Case, without persisting their text.
            "rendered_prompt_set_hash": descriptor.get("rendered_prompt_set_hash", "unavailable"),
            # AgentSpec is the A/B identity evidence.  The command bridge is
            # only an execution detail and must not become that identity.
            **({"agent_spec_snapshot": snapshot} if isinstance(snapshot, dict) else {}),
        })
    protocol: dict[str, Any] = {
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "comparison_intent": comparison_intent,
        "allowed_differences": sorted(set(allowed_differences)),
        "benchmark": {"cases": sorted(snapshots, key=lambda item: str(item["case_id"]))},
        "agents": protocol_agents,
        "model": {
            "provider": os.environ.get("AGENT_PROVIDER", "openai-compatible"),
            "model": os.environ.get("AGENT_MODEL", "unconfigured"),
            "base_url": os.environ.get("AGENT_BASE_URL", "default"),
            "temperature": _optional_float("AGENT_TEMPERATURE", 0.0, maximum=2.0),
            "top_p": _optional_float("AGENT_TOP_P", 1.0, maximum=1.0),
            "seed": _seed_snapshot(),
        },
        "execution": {"trials_per_case": trials, "schedule_seed": schedule_seed},
        "sandbox": {"docker": use_docker, "bash": bash, "image": "python:3.11-slim" if use_docker else None},
        "platform": {
            "python": platform.python_version(), "implementation": platform.python_implementation(),
            "system": platform.system(), "machine": platform.machine(),
        },
        "platform_source_hashes": source_hashes,
        "trace_schema_version": 1,
        "adapter_capabilities": adapter_capabilities,
    }
    protocol = _redact(protocol)
    protocol["protocol_fingerprint"] = protocol_fingerprint(protocol)
    return protocol


def compare_protocols(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Classify protocol equality without inventing version-governance facts."""

    if protocol_fingerprint(previous) == protocol_fingerprint(current):
        return {"level": "strict", "differences": []}
    differences = []
    for field in ("comparison_intent", "allowed_differences", "benchmark", "model", "execution", "sandbox", "platform", "platform_source_hashes", "trace_schema_version", "adapter_capabilities"):
        if previous.get(field) != current.get(field):
            differences.append(field)
    if previous.get("agents") != current.get("agents"):
        differences.append("agents")
    level = "not_comparable" if differences else "compatible"
    return {"level": level, "differences": differences}


def build_execution_plan(jobs: Iterable[dict[str, Any]], agents: list[dict[str, str]], *, seed: int) -> dict[str, Any]:
    """Create a repeatable, Trial-paired and interleaved Agent schedule."""

    if len(agents) < 2:
        raise ValueError("execution plan requires at least two agents")
    pairs = sorted((str(job["case_id"]), int(job["trial_index"]), str(job["job_id"])) for job in jobs)
    generator = random.Random(seed)
    generator.shuffle(pairs)
    entries: list[dict[str, Any]] = []
    for case_id, trial_index, job_id in pairs:
        order = list(agents)
        generator.shuffle(order)
        for agent in order:
            entries.append({
                "schedule_index": len(entries) + 1, "case_id": case_id, "trial_index": trial_index,
                "job_id": job_id, "agent_label": agent["id"], "agent_version": agent["version"],
            })
    return {"schema_version": 1, "seed": seed, "entries": entries}
