from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nanobot.rag.job_state import (
    JobArtifactValidator,
    JobRepository,
    JobStateError,
    RecoveryAction,
    RecoveryCoordinator,
    RetryPolicy,
    classify_job_error,
)
from nanobot.rag.store import RagStore
from nanobot.rag.types import (
    DocumentId,
    JobId,
    JobOperation,
    JobPhase,
    PrincipalId,
    RagErrorCode,
)


def _store(tmp_path: Path) -> RagStore:
    return RagStore.open(tmp_path, PrincipalId("a" * 64))


def _now() -> datetime:
    return datetime(2026, 8, 12, 10, 0, tzinfo=UTC)


def test_ingest_state_machine_persists_timestamps_and_safe_errors(tmp_path: Path) -> None:
    store = _store(tmp_path)
    repository = JobRepository(store, clock=_now)
    job_id = JobId("1" * 32)
    with store.connect() as connection:
        connection.execute(
            "INSERT INTO documents "
            "(document_id, display_name, content_sha256, mime_type, original_bytes, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "2" * 32,
                "document.txt",
                "a" * 64,
                "text/plain",
                10,
                "processing",
                int(_now().timestamp()),
                int(_now().timestamp()),
            ),
        )
        connection.execute(
            "INSERT INTO quota_reservations "
            "(reservation_id, job_id, reserved_bytes, created_at) VALUES (?, NULL, ?, ?)",
            ("3" * 32, 10, int(_now().timestamp())),
        )
    repository.create(
        job_id=job_id,
        operation=JobOperation.INGEST,
        channel="telegram",
        chat_id="private-chat",
        document_id=DocumentId("2" * 32),
        reservation_id="3" * 32,
    )

    for phase in (
        JobPhase.PARSING,
        JobPhase.CHUNKING,
        JobPhase.EMBEDDING,
        JobPhase.INDEXING,
        JobPhase.READY,
    ):
        repository.transition(job_id, phase)

    job = repository.get(job_id)
    assert job.phase is JobPhase.READY
    assert job.attempts == 0
    assert job.created_at == _now()
    assert job.updated_at == _now()
    assert job.error_code is None

    failed_id = JobId("4" * 32)
    repository.create(
        job_id=failed_id,
        operation=JobOperation.INGEST,
        channel="websocket",
        chat_id="chat",
    )
    repository.fail(failed_id, RagErrorCode.UNSAFE_DOCUMENT)
    assert repository.get(failed_id).error_code is RagErrorCode.UNSAFE_DOCUMENT


@pytest.mark.parametrize(
    ("operation", "from_phase", "to_phase"),
    [
        (JobOperation.INGEST, JobPhase.QUEUED, JobPhase.EMBEDDING),
        (JobOperation.INGEST, JobPhase.READY, JobPhase.FAILED),
        (JobOperation.DELETE, JobPhase.QUEUED, JobPhase.PARSING),
        (JobOperation.DELETE, JobPhase.DELETING, JobPhase.READY),
    ],
)
def test_state_machine_rejects_invalid_or_terminal_transitions(
    tmp_path: Path,
    operation: JobOperation,
    from_phase: JobPhase,
    to_phase: JobPhase,
) -> None:
    repository = JobRepository(_store(tmp_path), clock=_now)
    job_id = JobId("1" * 32)
    repository.create(
        job_id=job_id,
        operation=operation,
        channel="telegram",
        chat_id="chat",
    )
    if operation is JobOperation.DELETE:
        repository.transition(job_id, JobPhase.DELETING)
    elif from_phase is JobPhase.READY:
        for phase in (
            JobPhase.PARSING,
            JobPhase.CHUNKING,
            JobPhase.EMBEDDING,
            JobPhase.INDEXING,
            JobPhase.READY,
        ):
            repository.transition(job_id, phase)

    with pytest.raises(JobStateError, match="invalid job transition"):
        repository.transition(job_id, to_phase)


def test_delete_state_machine_is_queued_deleting_failed_or_complete(tmp_path: Path) -> None:
    repository = JobRepository(_store(tmp_path), clock=_now)
    ready = JobId("1" * 32)
    repository.create(
        job_id=ready,
        operation=JobOperation.DELETE,
        channel="telegram",
        chat_id="chat",
    )
    repository.transition(ready, JobPhase.DELETING)
    repository.complete_delete(ready)
    assert repository.get(ready).phase is JobPhase.READY

    failed = JobId("2" * 32)
    repository.create(
        job_id=failed,
        operation=JobOperation.DELETE,
        channel="telegram",
        chat_id="chat",
    )
    repository.transition(failed, JobPhase.DELETING)
    repository.fail(failed, RagErrorCode.INTERNAL_ERROR)
    assert repository.get(failed).phase is JobPhase.FAILED


def test_retry_policy_allows_two_transient_retries_and_never_retries_permanent() -> None:
    policy = RetryPolicy(max_transient_retries=2, base_delay_seconds=1.0)

    assert policy.next_delay(attempts=0, error=TimeoutError()) == 1.0
    assert policy.next_delay(attempts=1, error=ConnectionError()) == 2.0
    assert policy.next_delay(attempts=2, error=TimeoutError()) is None
    assert policy.next_delay(attempts=0, error=ValueError("bad document")) is None
    assert classify_job_error(TimeoutError()) == (
        RagErrorCode.INTERNAL_ERROR,
        True,
    )
    assert classify_job_error(ValueError()) == (
        RagErrorCode.UNSAFE_DOCUMENT,
        False,
    )


@dataclass
class FakeArtifactValidator(JobArtifactValidator):
    valid_phases: set[JobPhase]

    def validate(self, job_id: JobId, phase: JobPhase) -> bool:
        del job_id
        return phase in self.valid_phases


def test_startup_recovery_resumes_valid_phase_and_rewinds_invalid_artifact(
    tmp_path: Path,
) -> None:
    repository = JobRepository(_store(tmp_path), clock=_now)
    valid = JobId("1" * 32)
    invalid = JobId("2" * 32)
    for job_id in (valid, invalid):
        repository.create(
            job_id=job_id,
            operation=JobOperation.INGEST,
            channel="telegram",
            chat_id="chat",
        )
        repository.transition(job_id, JobPhase.PARSING)
        repository.transition(job_id, JobPhase.CHUNKING)

    actions = RecoveryCoordinator(
        repository,
        FakeArtifactValidator({JobPhase.CHUNKING}),
    ).recover_startup(
        artifact_overrides={invalid: set()},
    )

    assert actions == (
        RecoveryAction(valid, JobPhase.CHUNKING, JobPhase.CHUNKING, "resume"),
        RecoveryAction(invalid, JobPhase.CHUNKING, JobPhase.PARSING, "rewind"),
    )
    assert repository.get(invalid).phase is JobPhase.PARSING


def test_retry_is_persisted_and_exhaustion_marks_job_failed(tmp_path: Path) -> None:
    repository = JobRepository(_store(tmp_path), clock=_now)
    job_id = JobId("1" * 32)
    repository.create(
        job_id=job_id,
        operation=JobOperation.INGEST,
        channel="telegram",
        chat_id="chat",
    )
    repository.transition(job_id, JobPhase.PARSING)
    coordinator = RecoveryCoordinator(
        repository,
        FakeArtifactValidator({JobPhase.PARSING}),
        retry_policy=RetryPolicy(max_transient_retries=2, base_delay_seconds=0),
    )

    assert coordinator.record_failure(job_id, TimeoutError()) == 0
    assert coordinator.record_failure(job_id, TimeoutError()) == 0
    assert coordinator.record_failure(job_id, TimeoutError()) is None
    job = repository.get(job_id)
    assert job.attempts == 2
    assert job.phase is JobPhase.FAILED
    assert job.error_code is RagErrorCode.RETRY_EXHAUSTED


def test_expired_work_cleanup_is_exact_does_not_follow_symlink_and_keeps_live_jobs(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    repository = JobRepository(store, clock=_now)
    live_id = JobId("1" * 32)
    stale_id = JobId("2" * 32)
    repository.create(
        job_id=live_id,
        operation=JobOperation.INGEST,
        channel="telegram",
        chat_id="chat",
    )
    live = store.paths.prepare_work_directory(str(live_id))
    stale = store.paths.prepare_work_directory(str(stale_id))
    live.touch(exist_ok=True)
    stale.touch(exist_ok=True)
    old = (_now() - timedelta(days=40)).timestamp()
    for directory in (live, stale):
        directory.chmod(0o700)
        directory.touch()
        import os

        os.utime(directory, (old, old))

    outside = tmp_path / "outside"
    outside.mkdir()
    linked_id = "3" * 32
    (store.paths.work / linked_id).symlink_to(outside, target_is_directory=True)

    removed = RecoveryCoordinator(
        repository,
        FakeArtifactValidator(set()),
    ).cleanup_expired_work(retention=timedelta(days=30), now=_now())

    assert removed == (stale_id,)
    assert live.exists()
    assert not stale.exists()
    assert outside.exists()
    assert (store.paths.work / linked_id).is_symlink()
    shutil.rmtree(outside)
