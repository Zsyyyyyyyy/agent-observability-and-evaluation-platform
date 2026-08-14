"""SQLite source-of-truth Run Store with a recoverable JSONL audit outbox."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


class RunStore:
    """Persist Trial and scores atomically, then deliver JSONL audit records at-least-once.

    SQLite is authoritative. JSONL is an append-only projection: if its write
    fails after SQLite commits, the durable outbox is retried by the next Store
    instance. Consumers must de-duplicate by ``audit_id`` because a process may
    crash after an append and before acknowledging delivery.
    """

    def __init__(self, sqlite_path: str | Path, jsonl_path: str | Path | None = None):
        self.sqlite_path = Path(sqlite_path)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = Path(jsonl_path) if jsonl_path else self.sqlite_path.with_suffix(".jsonl")
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self.flush_audit_outbox()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.sqlite_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS trials (
                    trial_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, status TEXT NOT NULL,
                    result_json TEXT NOT NULL, recorded_at REAL NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS scores (
                    trial_id TEXT NOT NULL, evaluator TEXT NOT NULL, passed INTEGER NOT NULL,
                    score_json TEXT NOT NULL, recorded_at REAL NOT NULL,
                    PRIMARY KEY (trial_id, evaluator),
                    FOREIGN KEY (trial_id) REFERENCES trials(trial_id) ON DELETE CASCADE
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS attempts (
                    trial_id TEXT NOT NULL, attempt_id TEXT NOT NULL, trace_id TEXT NOT NULL,
                    status TEXT NOT NULL, selected INTEGER NOT NULL, result_json TEXT NOT NULL,
                    recorded_at REAL NOT NULL, PRIMARY KEY (trial_id, attempt_id)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS audit_outbox (
                    audit_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL, delivered_at REAL
                )"""
            )

    @staticmethod
    def _validate_trial(result: dict[str, Any]) -> tuple[str, str, str]:
        trial_id, trace_id, status = result.get("trial_id"), result.get("trace_id"), result.get("status")
        if not all(isinstance(value, str) and value for value in (trial_id, trace_id, status)):
            raise ValueError("trial result requires non-empty trial_id, trace_id, and status")
        return trial_id, trace_id, status

    @staticmethod
    def _validate_scores(scores: list[dict[str, Any]]) -> None:
        names = []
        for score in scores:
            evaluator = score.get("evaluator")
            if not isinstance(evaluator, str) or not evaluator:
                raise ValueError("score requires evaluator")
            names.append(evaluator)
        if len(names) != len(set(names)):
            raise ValueError("score evaluators must be unique per trial")

    @staticmethod
    def _canonical_payload(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True, separators=(",", ":"))

    def _write_trial(self, connection: sqlite3.Connection, result: dict[str, Any], recorded_at: float) -> str:
        trial_id, trace_id, status = self._validate_trial(result)
        payload = self._canonical_payload(result)
        connection.execute(
            """INSERT INTO trials(trial_id, trace_id, status, result_json, recorded_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(trial_id) DO UPDATE SET trace_id=excluded.trace_id, status=excluded.status,
               result_json=excluded.result_json, recorded_at=excluded.recorded_at""",
            (trial_id, trace_id, status, payload, recorded_at),
        )
        return payload

    def _write_scores(
        self, connection: sqlite3.Connection, trial_id: str, scores: list[dict[str, Any]], recorded_at: float
    ) -> None:
        self._validate_scores(scores)
        connection.execute("DELETE FROM scores WHERE trial_id = ?", (trial_id,))
        for score in scores:
            connection.execute(
                """INSERT INTO scores(trial_id, evaluator, passed, score_json, recorded_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (trial_id, score["evaluator"], int(bool(score.get("passed"))),
                 self._canonical_payload(score), recorded_at),
            )

    def _enqueue_audit(self, connection: sqlite3.Connection, payload: dict[str, Any], recorded_at: float) -> None:
        canonical = self._canonical_payload(payload)
        audit_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        envelope = {"audit_id": audit_id, **payload}
        connection.execute(
            "INSERT OR IGNORE INTO audit_outbox(audit_id, payload_json, created_at) VALUES (?, ?, ?)",
            (audit_id, self._canonical_payload(envelope), recorded_at),
        )

    def _write_attempt(self, connection: sqlite3.Connection, result: dict[str, Any], recorded_at: float) -> None:
        attempt_id = result.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            return
        trial_id, trace_id, status = self._validate_trial(result)
        connection.execute("UPDATE attempts SET selected = 0 WHERE trial_id = ?", (trial_id,))
        connection.execute(
            """INSERT INTO attempts(trial_id, attempt_id, trace_id, status, selected, result_json, recorded_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(trial_id, attempt_id) DO UPDATE SET trace_id=excluded.trace_id,
               status=excluded.status, selected=1, result_json=excluded.result_json,
               recorded_at=excluded.recorded_at""",
            (trial_id, attempt_id, trace_id, status, self._canonical_payload(result), recorded_at),
        )

    def record_run(self, result: dict[str, Any], scores: list[dict[str, Any]]) -> None:
        """Atomically commit a Trial, its complete score set, and its audit intent."""

        trial_id, _, _ = self._validate_trial(result)
        self._validate_scores(scores)
        recorded_at = time.time()
        with self._connect() as connection:
            self._write_trial(connection, result, recorded_at)
            self._write_scores(connection, trial_id, scores, recorded_at)
            self._write_attempt(connection, result, recorded_at)
            self._enqueue_audit(
                connection,
                {"schema_version": 1, "kind": "trial_recorded", "trial": result, "scores": scores,
                 "attempt_id": result.get("attempt_id"), "selected": bool(result.get("attempt_id"))},
                recorded_at,
            )
        self.flush_audit_outbox()

    def record_selected_projection(self, result: dict[str, Any], scores: list[dict[str, Any]], selected_attempt_id: str) -> None:
        """Write a Trial projection selected by the Artifact owner.

        This method never ranks Attempts.  The caller must have resolved the
        immutable ``selected-attempt.json`` projection first.
        """

        if result.get("attempt_id") != selected_attempt_id:
            raise ValueError("selected projection does not match selected attempt id")
        self.record_run(result, scores)

    # Compatibility methods for early scripts. New execution paths use record_run.
    def record_trial(self, result: dict[str, Any]) -> None:
        recorded_at = time.time()
        with self._connect() as connection:
            self._write_trial(connection, result, recorded_at)
            self._write_attempt(connection, result, recorded_at)
            self._enqueue_audit(connection, {"schema_version": 1, "kind": "trial_recorded", "trial": result,
                                              "attempt_id": result.get("attempt_id"), "selected": bool(result.get("attempt_id"))}, recorded_at)
        self.flush_audit_outbox()

    def record_scores(self, trial_id: str, scores: list[dict[str, Any]]) -> None:
        recorded_at = time.time()
        with self._connect() as connection:
            if not connection.execute("SELECT 1 FROM trials WHERE trial_id = ?", (trial_id,)).fetchone():
                raise ValueError(f"unknown trial_id: {trial_id}")
            self._write_scores(connection, trial_id, scores, recorded_at)

    def _append_audit(self, payload: str) -> None:
        with self.jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def flush_audit_outbox(self) -> int:
        """Deliver pending records. Raises on I/O failure without losing the intent."""

        with self._connect() as connection:
            pending = connection.execute(
                "SELECT audit_id, payload_json FROM audit_outbox WHERE delivered_at IS NULL ORDER BY created_at, audit_id"
            ).fetchall()
        for row in pending:
            self._append_audit(row["payload_json"])
            with self._connect() as connection:
                connection.execute(
                    "UPDATE audit_outbox SET delivered_at = ? WHERE audit_id = ? AND delivered_at IS NULL",
                    (time.time(), row["audit_id"]),
                )
        return len(pending)

    def pending_audit_count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM audit_outbox WHERE delivered_at IS NULL").fetchone()[0])

    def get_trial(self, trial_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT result_json FROM trials WHERE trial_id = ?", (trial_id,)).fetchone()
        return json.loads(row["result_json"]) if row else None

    def list_trials(self, status: str | None = None) -> list[dict[str, Any]]:
        query, args = "SELECT result_json FROM trials", ()
        if status is not None:
            query += " WHERE status = ?"
            args = (status,)
        query += " ORDER BY recorded_at, trial_id"
        with self._connect() as connection:
            rows = connection.execute(query, args).fetchall()
        return [json.loads(row["result_json"]) for row in rows]

    def get_scores(self, trial_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT score_json FROM scores WHERE trial_id = ? ORDER BY evaluator", (trial_id,)
            ).fetchall()
        return [json.loads(row["score_json"]) for row in rows]

    def list_attempts(self, trial_id: str) -> list[dict[str, Any]]:
        """Return retained physical executions in chronological order."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT result_json FROM attempts WHERE trial_id = ? ORDER BY recorded_at, attempt_id", (trial_id,)
            ).fetchall()
        return [json.loads(row["result_json"]) for row in rows]
