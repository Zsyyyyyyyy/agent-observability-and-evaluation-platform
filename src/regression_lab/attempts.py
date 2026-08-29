"""管理可恢复 Trial 下按 Attempt 隔离的产物生命周期。

Trial 表示 Case × Agent 版本 × 重复次数这一逻辑运行单元，Attempt 表示它的
一次物理执行。不同 Attempt 不共享工作目录、Trace 或 Agent 输出路径，避免上一次
执行的延迟进程污染重试证据。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from regression_lab.manifest import ManifestError, safe_child_path
from regression_lab.artifacts import write_json_atomically


ATTEMPT_SCHEMA_VERSION = 1
ATTEMPT_STATUSES = frozenset({"running", "completed", "timed_out", "invalid", "aborted"})


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def terminal_status_from_result(result: dict[str, Any]) -> str:
    """把 Worker 结果状态映射为物理 Attempt 的终态。"""

    if result.get("status") == "timed_out":
        return "timed_out"
    if result.get("status") == "trace_incomplete":
        return "invalid"
    return "completed"


@dataclass(frozen=True)
class AttemptPaths:
    """一次物理 Trial 执行独占的产物路径。"""

    attempt_id: str
    directory: Path

    @property
    def manifest(self) -> Path:
        return self.directory / "attempt-manifest.json"

    @property
    def input(self) -> Path:
        return self.directory / "trial-input.json"

    @property
    def result(self) -> Path:
        return self.directory / "result.json"

    @property
    def trace(self) -> Path:
        return self.directory / "trace.jsonl"

    @property
    def agent_output(self) -> Path:
        return self.directory / "agent-output.json"

    @property
    def worktree(self) -> Path:
        return self.directory / "worktree"


class AttemptManager:
    """在一个平台所有的 Job 下创建和读取不可变 Attempt。"""

    def __init__(self, job_dir: str | Path, *, job_id: str, fingerprint: str,
                 protocol_fingerprint: str | None = None, schedule_index: int | None = None):
        self.job_dir = Path(job_dir).resolve()
        self.job_id = job_id
        self.fingerprint = fingerprint
        self.protocol_fingerprint = protocol_fingerprint
        self.schedule_index = schedule_index
        self._lock_fd: int | None = None

    @property
    def attempts_dir(self) -> Path:
        return self.job_dir / "attempts"

    @property
    def selected_path(self) -> Path:
        return self.job_dir / "selected-attempt.json"

    @property
    def lock_path(self) -> Path:
        return self.job_dir / ".trial.lock"

    def acquire_trial_lock(self) -> None:
        """为逻辑 Trial 获取非阻塞排他锁。"""

        if self._lock_fd is not None:
            return
        self.job_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise RuntimeError(f"trial already running: {self.job_id}") from exc
        self._lock_fd = fd

    def release_trial_lock(self) -> None:
        if self._lock_fd is None:
            return
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(self._lock_fd)
            self._lock_fd = None

    def recover_orphaned_attempts(self) -> list[str]:
        """持有 Trial 锁后，恢复或终止遗留的 running Attempt。"""

        if self._lock_fd is None:
            raise RuntimeError("trial lock is required before recovering attempts")
        recovered: list[str] = []
        for paths in self.list_attempts():
            try:
                payload = self._load_owned_manifest(paths)
            except ValueError:
                continue
            if payload.get("status") != "running":
                continue
            try:
                result = self._read_result(paths)
            except ValueError:
                payload.update({"status": "aborted", "ended_at": _now(), "error": "recovered orphaned running attempt"})
                write_json_atomically(paths.manifest, payload)
            else:
                self.finish_attempt(
                    paths,
                    terminal_status_from_result(result),
                    error=result.get("error") if isinstance(result.get("error"), str) else None,
                )
            recovered.append(paths.attempt_id)
        return recovered

    def list_attempts(self) -> list[AttemptPaths]:
        if not self.attempts_dir.is_dir():
            return []
        paths: list[AttemptPaths] = []
        for directory in self.attempts_dir.iterdir():
            if not directory.is_dir():
                continue
            try:
                attempt_id = safe_child_path(self.attempts_dir, directory.name, "attempt id").name
            except ManifestError:
                continue
            if attempt_id.startswith("attempt_") and attempt_id[8:].isdigit():
                paths.append(AttemptPaths(attempt_id, directory))
        return sorted(paths, key=lambda path: int(path.attempt_id[8:]))

    def create_attempt(self) -> AttemptPaths:
        """分配下一个 Attempt，不修改此前证据。"""

        self.attempts_dir.mkdir(parents=True, exist_ok=True)
        next_index = max((int(path.attempt_id[8:]) for path in self.list_attempts()), default=0) + 1
        attempt_id = f"attempt_{next_index:03d}"
        directory = safe_child_path(self.attempts_dir, attempt_id, "attempt id")
        directory.mkdir()
        paths = AttemptPaths(attempt_id, directory)
        payload: dict[str, Any] = {
            "schema_version": ATTEMPT_SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "job_id": self.job_id,
            "fingerprint": self.fingerprint,
            "status": "running",
            "started_at": _now(),
        }
        if self.protocol_fingerprint:
            payload["protocol_fingerprint"] = self.protocol_fingerprint
        if self.schedule_index is not None:
            payload["schedule_index"] = self.schedule_index
        write_json_atomically(paths.manifest, payload)
        return paths

    def finish_attempt(self, paths: AttemptPaths, status: str, *, error: str | None = None) -> None:
        """最终结果持久化后，才把所属 Attempt 标记为终态。"""

        if status not in ATTEMPT_STATUSES - {"running"}:
            raise ValueError(f"unsupported terminal attempt status: {status}")
        payload = self._load_owned_manifest(paths)
        result_digest = self._result_digest(paths)
        payload.update({"status": status, "ended_at": _now(), "result_sha256": result_digest})
        if error:
            payload["error"] = error
        write_json_atomically(paths.manifest, payload)

    def select_attempt(self, paths: AttemptPaths, *, reason: str = "latest_terminal_attempt") -> None:
        """记录从不可变证据中选出的唯一 Trial 投影。

        选择器固定采用最新终态 Attempt，而不是历史最优结果。后续重试不能从当前
        Trial 视图中消失；可靠性分析仍可独立读取所有保留的 Attempt。
        """

        payload = self._load_owned_manifest(paths)
        if payload.get("status") == "running":
            raise ValueError("cannot select a running attempt")
        write_json_atomically(
            self.selected_path,
            {
                "schema_version": ATTEMPT_SCHEMA_VERSION,
                "job_id": self.job_id,
                "attempt_id": paths.attempt_id,
                "selection_policy": "latest_terminal_attempt_v1",
                "selection_reason": reason,
                "attempt_count": len(self.list_attempts()),
                "selected_at": _now(),
                **({"protocol_fingerprint": self.protocol_fingerprint} if self.protocol_fingerprint else {}),
                **({"schedule_index": self.schedule_index} if self.schedule_index is not None else {}),
            },
        )

    def resolve_selected_attempt(self) -> tuple[AttemptPaths, dict[str, Any]] | None:
        """读取当前投影，不重新执行选择策略。"""

        try:
            selected = json.loads(self.selected_path.read_text(encoding="utf-8"))
            attempt_id = selected.get("attempt_id") if isinstance(selected, dict) else None
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(attempt_id, str):
            return None
        for paths in self.list_attempts():
            if paths.attempt_id != attempt_id:
                continue
            try:
                manifest = self._load_owned_manifest(paths)
                result = self._read_result(paths)
            except (OSError, json.JSONDecodeError, ValueError):
                return None
            if manifest.get("status") == "running" or not self._result_matches_manifest(paths, manifest):
                return None
            return paths, result
        return None

    def select_latest_terminal_attempt(self) -> tuple[AttemptPaths, dict[str, Any]] | None:
        """从不可变证据中选择最新且可读的终态 Attempt。"""

        # list_attempts 已按 Attempt 序号升序排列；逆序读取即可得到最新有效证据，
        # 无需先收集全部候选再排序。
        for paths in reversed(self.list_attempts()):
            try:
                manifest = self._load_owned_manifest(paths)
                result = self._read_result(paths)
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if manifest.get("status") == "running" or not self._result_matches_manifest(paths, manifest):
                continue
            self.select_attempt(paths)
            return paths, result
        return None

    # 保留旧调用入口；新代码应直接使用语义明确的 select_latest_terminal_attempt。
    def select_best_attempt(self) -> tuple[AttemptPaths, dict[str, Any]] | None:
        return self.select_latest_terminal_attempt()

    def _load_owned_manifest(self, paths: AttemptPaths) -> dict[str, Any]:
        try:
            payload = json.loads(paths.manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"attempt manifest is unreadable: {paths.manifest}") from exc
        if not isinstance(payload, dict) or payload.get("job_id") != self.job_id:
            raise ValueError("attempt manifest does not belong to this job")
        if payload.get("fingerprint") != self.fingerprint or payload.get("attempt_id") != paths.attempt_id:
            raise ValueError("attempt manifest identity does not match")
        return payload

    @staticmethod
    def _read_result(paths: AttemptPaths) -> dict[str, Any]:
        try:
            result = json.loads(paths.result.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"attempt result is unreadable: {paths.result}") from exc
        if not isinstance(result, dict):
            raise ValueError(f"attempt result is not an object: {paths.result}")
        return result

    @staticmethod
    def _result_digest(paths: AttemptPaths) -> str:
        AttemptManager._read_result(paths)
        return "sha256:" + hashlib.sha256(paths.result.read_bytes()).hexdigest()

    def _result_matches_manifest(self, paths: AttemptPaths, manifest: dict[str, Any]) -> bool:
        expected = manifest.get("result_sha256")
        return not isinstance(expected, str) or expected == self._result_digest(paths)
