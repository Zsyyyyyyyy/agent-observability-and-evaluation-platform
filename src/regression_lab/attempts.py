"""Attempt-scoped Artifact lifecycle for resumable benchmark Trials.

A Trial is the logical Case × Agent Version × repetition unit.  An Attempt is
one physical execution of that Trial.  Attempt directories never share a
worktree, trace, or agent-output path, so a late process from one execution
cannot corrupt a retry.
"""

from __future__ import annotations

import json
import fcntl
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


@dataclass(frozen=True)
class AttemptPaths:
    """Paths exclusively owned by a single physical Trial execution."""

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
    """Create and inspect immutable Attempt directories under one owned Job."""

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
        """Acquire an exclusive non-blocking lock for this logical Trial."""

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
        """Mark stale running Attempts aborted after this process owns the Trial lock."""

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
            payload.update({"status": "aborted", "ended_at": _now(), "error": "recovered orphaned running attempt"})
            write_json_atomically(paths.manifest, payload)
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
        """Allocate the next Attempt without modifying prior evidence."""

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
        """Mark an owned Attempt terminal while retaining every Artifact."""

        if status not in ATTEMPT_STATUSES - {"running"}:
            raise ValueError(f"unsupported terminal attempt status: {status}")
        payload = self._load_owned_manifest(paths)
        payload.update({"status": status, "ended_at": _now()})
        if error:
            payload["error"] = error
        write_json_atomically(paths.manifest, payload)

    def select_attempt(self, paths: AttemptPaths, *, reason: str = "latest_terminal_attempt") -> None:
        """Record the sole Trial projection chosen from immutable evidence.

        The selector deliberately chooses the newest terminal Attempt, rather
        than the historical best pass.  A later retry must not disappear from
        the active Trial view; reliability analysis can still inspect every
        retained Attempt separately.
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
        """Resolve the current projection without recalculating selection."""

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
                result = json.loads(paths.result.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                return None
            if manifest.get("status") == "running" or not isinstance(result, dict):
                return None
            return paths, result
        return None

    def select_latest_terminal_attempt(self) -> tuple[AttemptPaths, dict[str, Any]] | None:
        """Select the newest readable terminal Attempt from immutable evidence."""

        candidates: list[tuple[int, AttemptPaths, dict[str, Any]]] = []
        for paths in self.list_attempts():
            try:
                manifest = self._load_owned_manifest(paths)
                result = json.loads(paths.result.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if manifest.get("status") == "running" or not isinstance(result, dict):
                continue
            candidates.append((int(paths.attempt_id[8:]), paths, result))
        if not candidates:
            return None
        _, paths, result = max(candidates, key=lambda item: item[0])
        self.select_attempt(paths)
        return paths, result

    # Compatibility for callers created before the selection policy was
    # explicit.  New code should call select_latest_terminal_attempt.
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
