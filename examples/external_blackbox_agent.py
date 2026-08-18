#!/usr/bin/env python3
"""Minimal non-instrumented Agent used to verify the Black-box smoke path."""

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    worktree = Path(args.workspace)
    target = worktree / "src" / "calculator.py"
    target.write_text(
        "def calculate(value):\n    return 0 if value == '' else int(value) + 1\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
