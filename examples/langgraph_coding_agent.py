#!/usr/bin/env python3
"""只在 Graph 启动点注入 Regression Lab Callback 的离线 LangGraph 示例。"""

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

from regression_lab_observer.langgraph import LangGraphObserver


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
    worktree: Path
    case_id: str
    agent_version: str
    target_path: str
    replacement: str


def planner(state: AgentState) -> dict:
    """真实 Agent 在此调用 LangChain 模型时，框架 Callback 会自动观测它。"""

    return {}


def _read_target(state: AgentState) -> None:
    (state["worktree"] / state["target_path"]).read_text(encoding="utf-8")


def coder(state: AgentState) -> dict:
    _read_target(state)
    # V2 复用首次读取结果；V1 仅多做一次相同读取，修复结果完全一致。
    if state["agent_version"] == "langgraph-agent-v1":
        _read_target(state)
    (state["worktree"] / state["target_path"]).write_text(state["replacement"], encoding="utf-8")
    return {}


def verifier(state: AgentState) -> dict:
    subprocess.run(
        shlex.split(os.environ["REGRESSION_TEST_COMMAND"]), cwd=state["worktree"],
        text=True, capture_output=True, check=False,
    )
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
        if profile is not None:
            profiles[version] = {
                "profile_id": profile,
                "rendered_prompt_set_hash": "sha256:" + hashlib.sha256(profile.encode("utf-8")).hexdigest(),
            }
    print(json.dumps({"profiles": profiles}, ensure_ascii=False))
    return 0


def main() -> int:
    if "--describe-protocol" in sys.argv:
        return describe_protocol()
    case_id = os.environ["REGRESSION_CASE_ID"]
    target = TARGETS.get(case_id)
    if target is None:
        raise ValueError(f"unsupported integration Case: {case_id}")
    version = os.environ["REGRESSION_AGENT_VERSION"]
    state: AgentState = {
        "worktree": Path(os.environ["REGRESSION_WORKTREE"]),
        "case_id": case_id,
        "agent_version": version,
        "target_path": target[0],
        "replacement": target[1],
    }
    # 唯一接入点：节点、工具和业务实现不感知 Regression Lab。
    with LangGraphObserver.from_environment() as observation:
        build_graph().invoke(state, config={"callbacks": [observation.callback]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
