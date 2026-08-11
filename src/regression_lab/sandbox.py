"""Docker-backed execution boundary for Agent tools and test commands."""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class SandboxConfig:
    image: str = "python:3.11-slim"
    network: str = "none"
    cpus: str = "1.0"
    memory: str = "512m"
    pids_limit: int = 128
    timeout_seconds: int = 180
    tmpfs_size: str = "64m"


@dataclass(frozen=True)
class SandboxResult:
    status: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    argv: tuple[str, ...]


class SandboxUnavailable(RuntimeError):
    """Raised when the Docker CLI or daemon cannot run a Sandbox trial."""


class DockerSandbox:
    """Run a shell command with a single Worktree mounted read/write."""

    def __init__(self, worktree: str | Path, config: SandboxConfig | None = None):
        self.worktree = Path(worktree).resolve()
        self.config = config or SandboxConfig()
        if not self.worktree.is_dir():
            raise ValueError(f"worktree does not exist: {self.worktree}")

    @staticmethod
    def docker_cli() -> str:
        docker = shutil.which("docker")
        if not docker:
            raise SandboxUnavailable("docker CLI is not installed")
        return docker

    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            docker = cls.docker_cli()
            result = subprocess.run(
                [docker, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (SandboxUnavailable, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
        if result.returncode != 0:
            return False, (result.stderr or result.stdout).strip() or "docker daemon unavailable"
        return True, result.stdout.strip()

    def command(self, shell_command: str, container_name: str | None = None) -> list[str]:
        """Build the exact docker argv; this is independently unit-testable."""

        docker = self.docker_cli()
        # Bind mounts are read/write by default; `rw` is not a valid
        # key=value field for Docker's long `--mount` syntax.
        mount = f"type=bind,src={self.worktree},dst=/workspace"
        argv = [
            docker,
            "run",
            "--rm",
            "--init",
            "--network",
            self.config.network,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--read-only",
            "--cpus",
            self.config.cpus,
            "--memory",
            self.config.memory,
            "--memory-swap",
            self.config.memory,
            "--pids-limit",
            str(self.config.pids_limit),
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={self.config.tmpfs_size}",
            "--mount",
            mount,
            "--workdir",
            "/workspace",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            self.config.image,
            "sh",
            "-lc",
            shell_command,
        ]
        if container_name:
            argv[3:3] = ["--name", container_name]
        return argv

    def _force_remove(self, container_name: str) -> str:
        """Remove a container left behind when the Docker client is interrupted."""

        try:
            result = subprocess.run(
                [self.docker_cli(), "rm", "--force", container_name],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return f"cleanup failed: {exc}"
        return (result.stderr or result.stdout or "").strip()

    def run(self, shell_command: str, timeout_seconds: int | None = None) -> SandboxResult:
        container_name = f"regression-lab-{uuid.uuid4().hex[:16]}"
        argv = tuple(self.command(shell_command, container_name=container_name))
        timeout = timeout_seconds or self.config.timeout_seconds
        started = time.monotonic()
        try:
            result = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SandboxUnavailable(str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            cleanup_detail = self._force_remove(container_name)
            return SandboxResult(
                status="timed_out",
                exit_code=-1,
                stdout=str(exc.stdout or ""),
                stderr=(
                    "docker sandbox command timed out"
                    + (f"; {cleanup_detail}" if cleanup_detail else "")
                ),
                duration_ms=(time.monotonic() - started) * 1000,
                argv=argv,
            )
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "").strip()
            daemon_markers = (
                "docker api",
                "cannot connect to the docker daemon",
                "failed to connect to the docker api",
                "is the docker daemon running",
            )
            if any(marker in error_text.lower() for marker in daemon_markers):
                raise SandboxUnavailable(error_text or "docker daemon unavailable")
        status = "completed" if result.returncode == 0 else "command_failed"
        return SandboxResult(
            status=status,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=(time.monotonic() - started) * 1000,
            argv=argv,
        )
