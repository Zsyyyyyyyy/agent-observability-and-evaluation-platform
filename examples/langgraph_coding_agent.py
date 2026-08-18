#!/usr/bin/env python3
"""A minimal LangGraph Coding Agent for the external-command contract."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from regression_lab.sdk import AgentObserver
from regression_lab.tool_semantics import semantic_tool_attributes


PROFILES = {
    "langgraph-agent-v1": "langgraph-coder-repeated-read-v1",
    "langgraph-agent-v2": "langgraph-coder-single-read-v2",
}
TARGETS = {
    "smoke_calculator_empty_input": ("src/calculator.py", "def calculate(value):\n    return 0 if value == '' else int(value) + 1\n"),
    "normalize_none_input": ("src/normalizer.py", "def normalize_name(value):\n    return '' if value is None else value.strip().lower()\n"),
    "parse_port_blank_default": ("src/config.py", "def parse_port(value):\n    return 8000 if value == '' else int(value)\n"),
}


class AgentState(TypedDict):
    observer: AgentObserver
    worktree: Path
    case_id: str
    agent_version: str
    target_path: str
    replacement: str


def _usage(span: object, prompt_tokens: int, completion_tokens: int) -> None:
    span.record_usage({
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    })


def planner(state: AgentState) -> dict:
    observer = state["observer"]
    with observer.span("workflow.planner", "workflow", node="Planner"):
        with observer.model_call(model="langgraph-deterministic-planner") as call:
            _usage(call, 100, 20)
    return {}


def _read_target(state: AgentState) -> None:
    observer, worktree, target_path = state["observer"], state["worktree"], state["target_path"]
    with observer.tool_call("read_file", **semantic_tool_attributes("read_file", {"path": target_path}, worktree=worktree)) as tool:
        tool.preview((worktree / target_path).read_text(encoding="utf-8"))


def coder(state: AgentState) -> dict:
    observer, worktree, version = state["observer"], state["worktree"], state["agent_version"]
    with observer.span("workflow.coder", "workflow", node="Coder"):
        with observer.model_call(model="langgraph-deterministic-coder") as call:
            _usage(call, 120, 30)
        if version == "langgraph-agent-failure-probe":
            with observer.tool_call("remove_worktree", **semantic_tool_attributes("remove_worktree", {}, worktree=worktree)):
                pass
        _read_target(state)
        # V2 reuses this observation; V1's only strategy difference is one
        # redundant read of the identical file before the same edit.
        if version == "langgraph-agent-v1":
            _read_target(state)
        target_path = state["target_path"]
        with observer.tool_call(
            "edit_file",
            **semantic_tool_attributes("edit_file", {"path": target_path, "new_text": "[redacted]"}, worktree=worktree),
        ) as tool:
            (worktree / target_path).write_text(state["replacement"], encoding="utf-8")
            tool.preview(f"updated {target_path}")
    return {}


def verifier(state: AgentState) -> dict:
    observer, worktree = state["observer"], state["worktree"]
    with observer.span("workflow.verifier", "workflow", node="Verifier"):
        with observer.model_call(model="langgraph-deterministic-verifier") as call:
            _usage(call, 90, 15)
        with observer.tool_call("bash", **semantic_tool_attributes("bash", {"command": "configured_test"}, worktree=worktree)) as tool:
            completed = subprocess.run(
                shlex.split(os.environ["REGRESSION_TEST_COMMAND"]), cwd=worktree,
                text=True, capture_output=True, check=False,
            )
            if completed.returncode:
                tool.end("error", exit_code=completed.returncode)
            else:
                tool.preview("verification passed")
    return {}


def build_graph() -> object:
    graph = StateGraph(AgentState)
    graph.add_node("Planner", planner)
    graph.add_node("Coder", coder)
    graph.add_node("Verifier", verifier)
    graph.add_edge(START, "Planner")
    graph.add_edge("Planner", "Coder")
    graph.add_edge("Coder", "Verifier")
    graph.add_edge("Verifier", END)
    return graph.compile()


def describe_protocol() -> int:
    try:
        request = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return 2
    versions = request.get("versions", []) if isinstance(request, dict) else []
    profiles = {}
    for version in versions:
        profile = PROFILES.get(version)
        if profile is None:
            continue
        digest = hashlib.sha256(profile.encode("utf-8")).hexdigest()
        profiles[version] = {"profile_id": profile, "rendered_prompt_set_hash": f"sha256:{digest}"}
    print(json.dumps({"profiles": profiles}, ensure_ascii=False))
    return 0


def main() -> int:
    if "--describe-protocol" in sys.argv:
        return describe_protocol()
    observer = AgentObserver.from_environment()
    case_id = os.environ["REGRESSION_CASE_ID"]
    target = TARGETS.get(case_id)
    if target is None:
        raise ValueError(f"unsupported integration Case: {case_id}")
    version = os.environ["REGRESSION_AGENT_VERSION"]
    profile = PROFILES.get(version, "langgraph-failure-probe")
    state: AgentState = {
        "observer": observer,
        "worktree": Path(os.environ["REGRESSION_WORKTREE"]),
        "case_id": case_id,
        "agent_version": version,
        "target_path": target[0],
        "replacement": target[1],
    }
    with observer.run(agent_profile=profile):
        build_graph().invoke(state)
    AgentObserver.write_agent_output("LangGraph workflow completed.", "workflow_completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
