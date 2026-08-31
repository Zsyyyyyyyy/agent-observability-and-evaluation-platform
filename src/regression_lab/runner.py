"""Bounded subprocess helpers used by the benchmark parent runner."""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ProcessResult:
    """Outcome of a worker process, including a parent-enforced deadline."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


def terminate_process_group(process: subprocess.Popen[str], *, grace_seconds: float = 1.0) -> None:
    """Stop a session leader and all of its descendants without broad signals.

    Callers only pass processes created with ``start_new_session=True``.  The
    process id is therefore also the isolated process-group id; no unrelated
    local process can be targeted.
    """

    try:
        # 父进程可能已退出而子进程仍持有 stdout/stderr；此时仍必须按原 PGID 清理。
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.communicate(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_with_deadline(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
) -> ProcessResult:
    """Run a worker in its own process group and terminate the whole group on timeout."""

    # communicate 同时消费两条管道，避免 Agent 大量输出时父进程与子进程相互等待。
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=dict(env),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return ProcessResult(process.returncode, stdout, stderr, timed_out=False)
    except subprocess.TimeoutExpired:
        terminate_process_group(process)
        stdout, stderr = process.communicate()
        return ProcessResult(
            returncode=process.returncode if process.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
        )
