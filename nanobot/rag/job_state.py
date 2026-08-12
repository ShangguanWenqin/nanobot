"""Persistent RAG job state transitions, retry policy, and startup recovery."""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol

from nanobot.rag.store import RagStore
from nanobot.rag.types import (
    DocumentId,
    JobId,
    JobOperation,
    JobPhase,
    RagErrorCode,
    RagJob,
)


class JobStateError(RuntimeError):
    pass


_INGEST_NEXT = {
    JobPhase.QUEUED: JobPhase.PARSING,
    JobPhase.PARSING: JobPhase.CHUNKING,
    JobPhase.CHUNKING: JobPhase.EMBEDDING,
    JobPhase.EMBEDDING: JobPhase.INDEXING,
    JobPhase.INDEXING: JobPhase.READY,
}

_REWIND_PHASE = {
    JobPhase.CHUNKING: JobPhase.PARSING,
    JobPhase.EMBEDDING: JobPhase.CHUNKING,
    JobPhase.INDEXING: JobPhase.EMBEDDING,
}

_TERMINAL_PHASES = {JobPhase.READY, JobPhase.FAILED}


class JobRepository:
    def __init__(self, store: RagStore, *, clock: Callable[[], datetime]) -> None:
        self.store = store
        self.clock = clock

    def create(
        self,
        *,
        job_id: JobId,
        operation: JobOperation,
        channel: str,
        chat_id: str,
        document_id: DocumentId | None = None,
        reservation_id: str | None = None,
    ) -> RagJob:
        if not channel.strip() or not chat_id.strip():
            raise ValueError("job channel and chat ID must not be empty")
        timestamp = _timestamp(self.clock())
        with self.store.connect() as connection:
            connection.execute(
                "INSERT INTO jobs "
                "(job_id, operation, phase, attempts, document_id, reservation_id, "
                "channel, chat_id, error_code, created_at, updated_at) "
                "VALUES (?, ?, ?, 0, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    str(job_id),
                    operation.value,
                    JobPhase.QUEUED.value,
                    str(document_id) if document_id is not None else None,
                    reservation_id,
                    channel,
                    chat_id,
                    timestamp,
                    timestamp,
                ),
            )
        return self.get(job_id)

    def get(self, job_id: JobId) -> RagJob:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT job_id, operation, phase, attempts, document_id, error_code, "
                "created_at, updated_at FROM jobs WHERE job_id = ?",
                (str(job_id),),
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _row_to_job(row)

    def transition(self, job_id: JobId, target: JobPhase) -> RagJob:
        current = self.get(job_id)
        valid = (
            _INGEST_NEXT.get(current.phase) is target
            if current.operation is JobOperation.INGEST
            else current.phase is JobPhase.QUEUED and target is JobPhase.DELETING
        )
        if not valid:
            raise JobStateError(
                f"invalid job transition: {current.operation.value} "
                f"{current.phase.value} -> {target.value}"
            )
        self._set_phase(job_id, target, error_code=None)
        return self.get(job_id)

    def complete_delete(self, job_id: JobId) -> RagJob:
        current = self.get(job_id)
        if current.operation is not JobOperation.DELETE or current.phase is not JobPhase.DELETING:
            raise JobStateError("invalid job transition: deletion is not active")
        self._set_phase(job_id, JobPhase.READY, error_code=None)
        return self.get(job_id)

    def fail(self, job_id: JobId, error_code: RagErrorCode) -> RagJob:
        current = self.get(job_id)
        if current.phase in _TERMINAL_PHASES:
            raise JobStateError("invalid job transition: terminal job cannot fail")
        self._set_phase(job_id, JobPhase.FAILED, error_code=error_code)
        return self.get(job_id)

    def increment_attempts(self, job_id: JobId) -> RagJob:
        timestamp = _timestamp(self.clock())
        with self.store.connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET attempts = attempts + 1, updated_at = ? "
                "WHERE job_id = ? AND phase NOT IN (?, ?)",
                (
                    timestamp,
                    str(job_id),
                    JobPhase.READY.value,
                    JobPhase.FAILED.value,
                ),
            )
            if cursor.rowcount != 1:
                raise JobStateError("cannot retry a missing or terminal job")
        return self.get(job_id)

    def rewind(self, job_id: JobId, target: JobPhase) -> RagJob:
        current = self.get(job_id)
        if _REWIND_PHASE.get(current.phase) is not target:
            raise JobStateError("invalid job transition: unsafe recovery rewind")
        self._set_phase(job_id, target, error_code=None)
        return self.get(job_id)

    def active(self) -> tuple[RagJob, ...]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT job_id, operation, phase, attempts, document_id, error_code, "
                "created_at, updated_at FROM jobs WHERE phase NOT IN (?, ?) "
                "ORDER BY created_at, job_id",
                (JobPhase.READY.value, JobPhase.FAILED.value),
            ).fetchall()
        return tuple(_row_to_job(row) for row in rows)

    def all_job_ids(self) -> frozenset[str]:
        with self.store.connect() as connection:
            rows = connection.execute("SELECT job_id FROM jobs").fetchall()
        return frozenset(str(row[0]) for row in rows)

    def _set_phase(
        self,
        job_id: JobId,
        phase: JobPhase,
        *,
        error_code: RagErrorCode | None,
    ) -> None:
        timestamp = _timestamp(self.clock())
        with self.store.connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET phase = ?, error_code = ?, updated_at = ? WHERE job_id = ?",
                (
                    phase.value,
                    error_code.value if error_code is not None else None,
                    timestamp,
                    str(job_id),
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_transient_retries: int = 2
    base_delay_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.max_transient_retries < 0:
            raise ValueError("maximum transient retries must not be negative")
        if self.base_delay_seconds < 0:
            raise ValueError("retry base delay must not be negative")

    def next_delay(self, *, attempts: int, error: Exception) -> float | None:
        _, transient = classify_job_error(error)
        if not transient or attempts >= self.max_transient_retries:
            return None
        return self.base_delay_seconds * (2**attempts)


def classify_job_error(error: Exception) -> tuple[RagErrorCode, bool]:
    if isinstance(error, (TimeoutError, ConnectionError)):
        return RagErrorCode.INTERNAL_ERROR, True
    if isinstance(error, ValueError):
        return RagErrorCode.UNSAFE_DOCUMENT, False
    return RagErrorCode.INTERNAL_ERROR, False


class JobArtifactValidator(Protocol):
    def validate(self, job_id: JobId, phase: JobPhase) -> bool: ...


@dataclass(frozen=True, slots=True)
class RecoveryAction:
    job_id: JobId
    previous_phase: JobPhase
    resumed_phase: JobPhase
    action: str


class RecoveryCoordinator:
    def __init__(
        self,
        repository: JobRepository,
        artifact_validator: JobArtifactValidator,
        *,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.artifact_validator = artifact_validator
        self.retry_policy = retry_policy or RetryPolicy()

    def recover_startup(
        self,
        *,
        artifact_overrides: Mapping[JobId, set[JobPhase]] | None = None,
    ) -> tuple[RecoveryAction, ...]:
        overrides = artifact_overrides or {}
        actions: list[RecoveryAction] = []
        for job in self.repository.active():
            phase = job.phase
            valid = (
                phase in overrides[job.job_id]
                if job.job_id in overrides
                else self.artifact_validator.validate(job.job_id, phase)
            )
            if valid or phase in {JobPhase.QUEUED, JobPhase.PARSING, JobPhase.DELETING}:
                actions.append(RecoveryAction(job.job_id, phase, phase, "resume"))
                continue
            rewind = _REWIND_PHASE.get(phase)
            if rewind is None:
                self.repository.fail(job.job_id, RagErrorCode.INDEXING_FAILED)
                actions.append(
                    RecoveryAction(job.job_id, phase, JobPhase.FAILED, "fail")
                )
                continue
            self.repository.rewind(job.job_id, rewind)
            actions.append(RecoveryAction(job.job_id, phase, rewind, "rewind"))
        return tuple(actions)

    def record_failure(self, job_id: JobId, error: Exception) -> float | None:
        job = self.repository.get(job_id)
        delay = self.retry_policy.next_delay(attempts=job.attempts, error=error)
        if delay is not None:
            self.repository.increment_attempts(job_id)
            return delay
        code, transient = classify_job_error(error)
        self.repository.fail(
            job_id,
            RagErrorCode.RETRY_EXHAUSTED if transient else code,
        )
        return None

    def cleanup_expired_work(
        self,
        *,
        retention: timedelta,
        now: datetime,
    ) -> tuple[JobId, ...]:
        if retention.total_seconds() < 0:
            raise ValueError("work retention must not be negative")
        cutoff = _timestamp(now - retention)
        known_jobs = self.repository.all_job_ids()
        removed: list[JobId] = []
        for path in self.repository.store.paths.work.iterdir():
            if path.is_symlink() or not path.is_dir():
                continue
            if path.name in known_jobs:
                continue
            try:
                job_id = JobId(_validate_job_directory_name(path.name))
                modified = int(path.stat().st_mtime)
            except (OSError, ValueError):
                continue
            if modified >= cutoff:
                continue
            self._remove_exact_work_directory(path)
            removed.append(job_id)
        return tuple(sorted(removed, key=str))

    def _remove_exact_work_directory(self, path: Path) -> None:
        work_root = self.repository.store.paths.work
        if path.parent != work_root or path.is_symlink() or not path.is_dir():
            raise JobStateError("refusing to remove unsafe RAG work directory")
        shutil.rmtree(path)


def _row_to_job(row: sqlite3.Row) -> RagJob:
    return RagJob(
        job_id=JobId(str(row["job_id"])),
        operation=JobOperation(str(row["operation"])),
        phase=JobPhase(str(row["phase"])),
        attempts=int(row["attempts"]),
        document_id=(
            DocumentId(str(row["document_id"]))
            if row["document_id"] is not None
            else None
        ),
        error_code=(
            RagErrorCode(str(row["error_code"]))
            if row["error_code"] is not None
            else None
        ),
        created_at=datetime.fromtimestamp(int(row["created_at"]), tz=UTC),
        updated_at=datetime.fromtimestamp(int(row["updated_at"]), tz=UTC),
    )


def _timestamp(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("job timestamps must be timezone-aware")
    return int(value.timestamp())


def _validate_job_directory_name(value: str) -> str:
    if len(value) != 32 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("invalid managed job directory name")
    return value


__all__ = [
    "JobArtifactValidator",
    "JobRepository",
    "JobStateError",
    "RecoveryAction",
    "RecoveryCoordinator",
    "RetryPolicy",
    "classify_job_error",
]
