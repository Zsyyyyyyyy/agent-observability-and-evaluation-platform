#!/usr/bin/env python3
"""Minimal external Agent example for the smoke_calculator Benchmark."""

from __future__ import annotations

import os
from pathlib import Path

from regression_lab.sdk import AgentObserver
from regression_lab.tool_semantics import semantic_tool_attributes


def main() -> None:
    observer = AgentObserver.from_environment()
    worktree = Path(os.environ["REGRESSION_WORKTREE"])
    target = worktree / "src" / "calculator.py"
    with observer.run():
        with observer.tool_call(
            "edit_file",
            **semantic_tool_attributes("edit_file", {"path": "src/calculator.py", "old_text": "", "new_text": ""}, worktree=worktree),
        ) as tool:
            target.write_text(
                "def calculate(value):\n    return 0 if value == '' else int(value) + 1\n",
                encoding="utf-8",
            )
            tool.preview("updated src/calculator.py")
        observer.event("agent.stop", reason="example_completed")
    AgentObserver.write_agent_output("Fixed empty input handling.", "example_completed")


if __name__ == "__main__":
    main()
