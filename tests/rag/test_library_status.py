from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from nanobot.rag.library_status import LibraryStatusService, RuntimeProfileReport
from nanobot.rag.quota import QuotaManager
from nanobot.rag.store import RagStore
from nanobot.rag.types import PrincipalId


class DiskProbe:
    def free_bytes(self, path: Path) -> int:
        del path
        return 10**9

    def used_bytes(self, path: Path) -> int:
        del path
        return 0


def _service(tmp_path: Path) -> tuple[LibraryStatusService, RagStore]:
    store = RagStore.open(tmp_path, PrincipalId("a" * 64))
    quota = QuotaManager(
        store,
        per_user_quota_bytes=1000,
        global_max_bytes=10**9,
        min_free_disk_bytes=0,
        disk_probe=DiskProbe(),
    )
    service = LibraryStatusService(
        store,
        quota,
        runtime_profile=RuntimeProfileReport(
            query_embedding="coreml-float32",
            batch_embedding="cpu-float32",
            reranker="coreml-float32",
            embedding_profile_id="e5-v1",
            reranker_profile_id="bge-v1",
        ),
        clock=lambda: datetime(2026, 8, 12, tzinfo=UTC),
        job_retention=timedelta(days=30),
    )
    return service, store


def _seed(store: RagStore) -> None:
    managed = store.paths.prepare_original_directory("1" * 32) / "secret.txt"
    managed.write_text("secret")
    with store.connect() as connection:
        for number, status in ((1, "ready"), (2, "processing"), (3, "failed")):
            connection.execute(
                "INSERT INTO documents "
                "(document_id, display_name, content_sha256, mime_type, original_bytes, "
                "status, created_at, updated_at, original_relpath) "
                "VALUES (?, ?, ?, 'text/plain', ?, ?, ?, ?, ?)",
                (
                    f"{number:032x}",
                    f"document-{number}.txt",
                    f"{number:064x}",
                    number * 10,
                    status,
                    number,
                    number,
                    managed.relative_to(store.paths.principal_root).as_posix(),
                ),
            )
        connection.execute(
            "UPDATE quota_ledger SET committed_bytes = 40, reserved_bytes = 20 "
            "WHERE singleton = 1"
        )
        for number, phase, updated_at in (
            (1, "ready", int(datetime(2026, 8, 11, tzinfo=UTC).timestamp())),
            (2, "failed", int(datetime(2026, 6, 1, tzinfo=UTC).timestamp())),
            (3, "embedding", int(datetime(2026, 8, 12, tzinfo=UTC).timestamp())),
        ):
            connection.execute(
                "INSERT INTO jobs "
                "(job_id, operation, phase, attempts, channel, chat_id, error_code, "
                "created_at, updated_at) VALUES (?, 'ingest', ?, 0, 'websocket', 'chat', "
                "NULL, ?, ?)",
                (f"{number + 10:032x}", phase, updated_at, updated_at),
            )


def test_document_list_is_stable_paginated_and_never_exposes_host_paths(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    _seed(store)

    first = service.list_documents(limit=2)
    second = service.list_documents(limit=2, cursor=first.next_cursor)

    assert [item.filename for item in first.items] == ["document-3.txt", "document-2.txt"]
    assert [item.filename for item in second.items] == ["document-1.txt"]
    assert first.next_cursor is not None
    rendered = repr((first, second))
    assert str(store.paths.principal_root) not in rendered
    assert "original_relpath" not in rendered


def test_status_reports_quota_counts_active_recent_jobs_and_runtime_profiles(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    _seed(store)

    status = service.status(recent_limit=10)

    assert status.quota.committed_bytes == 40
    assert status.quota.reserved_bytes == 20
    assert status.ready_document_count == 1
    assert [job.phase.value for job in status.active_jobs] == ["embedding"]
    assert [job.phase.value for job in status.recent_jobs] == ["embedding", "ready"]
    assert status.runtime.query_embedding == "coreml-float32"
    assert str(store.paths.principal_root) not in repr(status)


def test_retention_cleanup_removes_only_expired_terminal_job_metadata(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _seed(store)

    removed = service.prune_expired_jobs()

    assert removed == 1
    with store.connect() as connection:
        phases = [row[0] for row in connection.execute("SELECT phase FROM jobs").fetchall()]
    assert sorted(phases) == ["embedding", "ready"]
