"""Path-free document, task, quota, and local-runtime status projections."""

from __future__ import annotations

import base64
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from nanobot.rag.job_state import JobRepository
from nanobot.rag.quota import QuotaManager, QuotaUsage
from nanobot.rag.store import RagStore
from nanobot.rag.types import (
    DocumentId,
    DocumentStatus,
    JobId,
    JobOperation,
    JobPhase,
    RagErrorCode,
    RagJob,
)


@dataclass(frozen=True, slots=True)
class RuntimeProfileReport:
    query_embedding: str
    batch_embedding: str
    reranker: str
    embedding_profile_id: str
    reranker_profile_id: str


@dataclass(frozen=True, slots=True)
class DocumentSummary:
    document_id: DocumentId
    filename: str
    mime_type: str
    original_bytes: int
    status: DocumentStatus
    created_at: datetime
    updated_at: datetime
    error_code: RagErrorCode | None


@dataclass(frozen=True, slots=True)
class DocumentPage:
    items: tuple[DocumentSummary, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class LibraryStatus:
    quota: QuotaUsage
    ready_document_count: int
    active_jobs: tuple[RagJob, ...]
    recent_jobs: tuple[RagJob, ...]
    runtime: RuntimeProfileReport


class LibraryStatusService:
    def __init__(
        self,
        store: RagStore,
        quota: QuotaManager,
        *,
        runtime_profile: RuntimeProfileReport,
        clock: Callable[[], datetime],
        job_retention: timedelta,
    ) -> None:
        if job_retention.total_seconds() < 0:
            raise ValueError("job retention must not be negative")
        self.store = store
        self.quota = quota
        self.runtime_profile = runtime_profile
        self.clock = clock
        self.job_retention = job_retention
        self.jobs = JobRepository(store, clock=clock)

    def list_documents(self, *, limit: int = 20, cursor: str | None = None) -> DocumentPage:
        if not 1 <= limit <= 100:
            raise ValueError("document page limit must be between 1 and 100")
        cursor_values = _decode_cursor(cursor) if cursor is not None else None
        query = (
            "SELECT document_id, display_name, mime_type, original_bytes, status, "
            "created_at, updated_at, error_code FROM documents "
        )
        parameters: tuple[object, ...]
        if cursor_values is None:
            query += "ORDER BY created_at DESC, document_id DESC LIMIT ?"
            parameters = (limit + 1,)
        else:
            query += (
                "WHERE created_at < ? OR (created_at = ? AND document_id < ?) "
                "ORDER BY created_at DESC, document_id DESC LIMIT ?"
            )
            parameters = (*cursor_values, limit + 1)
        with self.store.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        visible = rows[:limit]
        next_cursor = None
        if len(rows) > limit and visible:
            last = visible[-1]
            next_cursor = _encode_cursor(int(last["created_at"]), str(last["document_id"]))
        return DocumentPage(
            items=tuple(_document_from_row(row) for row in visible),
            next_cursor=next_cursor,
        )

    def status(self, *, recent_limit: int = 10) -> LibraryStatus:
        if not 1 <= recent_limit <= 100:
            raise ValueError("recent job limit must be between 1 and 100")
        cutoff = int((self.clock() - self.job_retention).timestamp())
        with self.store.connect() as connection:
            ready_count = int(
                connection.execute(
                    "SELECT count(*) FROM documents WHERE status = 'ready'"
                ).fetchone()[0]
            )
            rows = connection.execute(
                "SELECT job_id, operation, phase, attempts, document_id, error_code, "
                "created_at, updated_at FROM jobs WHERE updated_at >= ? "
                "ORDER BY updated_at DESC, job_id DESC LIMIT ?",
                (cutoff, recent_limit),
            ).fetchall()
        recent = tuple(_job_from_row(row) for row in rows)
        return LibraryStatus(
            quota=self.quota.usage(),
            ready_document_count=ready_count,
            active_jobs=self.jobs.active(),
            recent_jobs=recent,
            runtime=self.runtime_profile,
        )

    def prune_expired_jobs(self) -> int:
        cutoff = int((self.clock() - self.job_retention).timestamp())
        with self.store.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM jobs WHERE phase IN ('ready', 'failed') AND updated_at < ?",
                (cutoff,),
            )
            return cursor.rowcount


def _document_from_row(row: sqlite3.Row) -> DocumentSummary:
    return DocumentSummary(
        document_id=DocumentId(str(row["document_id"])),
        filename=str(row["display_name"]),
        mime_type=str(row["mime_type"]),
        original_bytes=int(row["original_bytes"]),
        status=DocumentStatus(str(row["status"])),
        created_at=datetime.fromtimestamp(int(row["created_at"]), tz=UTC),
        updated_at=datetime.fromtimestamp(int(row["updated_at"]), tz=UTC),
        error_code=(
            RagErrorCode(str(row["error_code"])) if row["error_code"] is not None else None
        ),
    )


def _job_from_row(row: sqlite3.Row) -> RagJob:
    return RagJob(
        job_id=JobId(str(row["job_id"])),
        operation=JobOperation(str(row["operation"])),
        phase=JobPhase(str(row["phase"])),
        attempts=int(row["attempts"]),
        document_id=(
            DocumentId(str(row["document_id"])) if row["document_id"] is not None else None
        ),
        error_code=(
            RagErrorCode(str(row["error_code"])) if row["error_code"] is not None else None
        ),
        created_at=datetime.fromtimestamp(int(row["created_at"]), tz=UTC),
        updated_at=datetime.fromtimestamp(int(row["updated_at"]), tz=UTC),
    )


def _encode_cursor(created_at: int, document_id: str) -> str:
    raw = json.dumps([created_at, document_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[int, int, str]:
    try:
        padding = "=" * (-len(value) % 4)
        raw: object = json.loads(base64.urlsafe_b64decode(value + padding))
        if not isinstance(raw, list):
            raise ValueError
        decoded = cast(list[object], raw)
        if (
            len(decoded) != 2
            or not isinstance(decoded[0], int)
            or not isinstance(decoded[1], str)
            or not decoded[1]
        ):
            raise ValueError
        return decoded[0], decoded[0], decoded[1]
    except Exception as exc:
        raise ValueError("invalid document page cursor") from exc


__all__ = [
    "DocumentPage",
    "DocumentSummary",
    "LibraryStatus",
    "LibraryStatusService",
    "RuntimeProfileReport",
]
