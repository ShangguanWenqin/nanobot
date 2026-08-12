"""Persistent deletion that hides first and releases quota only after durable purge."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import datetime

from nanobot.bus.events import ConversationScope
from nanobot.rag.chunking import EmbeddingInputBuilder
from nanobot.rag.job_state import JobRepository
from nanobot.rag.lexical import LexicalRepository
from nanobot.rag.protocols import Embedder
from nanobot.rag.store import RagStore
from nanobot.rag.types import (
    DocumentId,
    JobId,
    JobOperation,
    JobPhase,
    RagErrorCode,
    RagJob,
    RagRequestContext,
    SourceKind,
    SourceLocation,
)
from nanobot.rag.vector_store import VectorGenerationRepository

JobPhaseCallback = Callable[
    [JobPhase, DocumentId | None, RagErrorCode | None],
    Awaitable[None],
]


class RagDeletionService:
    def __init__(
        self,
        *,
        store: RagStore,
        embedder: Embedder,
        input_builder: EmbeddingInputBuilder,
        vectors: VectorGenerationRepository,
        lexical: LexicalRepository,
        clock: Callable[[], datetime],
        id_factory: Callable[[], str],
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.input_builder = input_builder
        self.vectors = vectors
        self.lexical = lexical
        self.clock = clock
        self.id_factory = id_factory
        self.jobs = JobRepository(store, clock=clock)

    def request_delete(
        self,
        context: RagRequestContext,
        document_id: DocumentId,
    ) -> JobId | None:
        self._authorize(context)
        job_id = JobId(self.id_factory())
        timestamp = int(self.clock().timestamp())
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            document = connection.execute(
                "SELECT status FROM documents WHERE document_id = ?",
                (str(document_id),),
            ).fetchone()
            if document is None or str(document["status"]) not in {"ready", "deleting"}:
                return None
            if str(document["status"]) == "deleting":
                existing = connection.execute(
                    "SELECT job_id FROM jobs WHERE document_id = ? AND operation = 'delete' "
                    "AND phase NOT IN ('ready', 'failed') ORDER BY created_at LIMIT 1",
                    (str(document_id),),
                ).fetchone()
                return JobId(str(existing["job_id"])) if existing is not None else None
            connection.execute(
                "UPDATE documents SET status = 'deleting', updated_at = ? "
                "WHERE document_id = ? AND status = 'ready'",
                (timestamp, str(document_id)),
            )
            connection.execute(
                "INSERT INTO jobs "
                "(job_id, operation, phase, attempts, document_id, reservation_id, "
                "channel, chat_id, error_code, created_at, updated_at) "
                "VALUES (?, 'delete', 'queued', 0, ?, NULL, ?, ?, NULL, ?, ?)",
                (
                    str(job_id),
                    str(document_id),
                    context.channel,
                    context.chat_id,
                    timestamp,
                    timestamp,
                ),
            )
        return job_id

    async def process_job(
        self,
        job_id: JobId,
        *,
        on_phase: JobPhaseCallback | None = None,
    ) -> RagJob:
        generation_id: str | None = None
        try:
            job = self.jobs.get(job_id)
            if job.operation is not JobOperation.DELETE or job.document_id is None:
                raise ValueError("deletion job has no document")
            if job.phase is JobPhase.QUEUED:
                self.jobs.transition(job_id, JobPhase.DELETING)
                await _notify_phase(on_phase, JobPhase.DELETING, job.document_id, None)
            elif job.phase is not JobPhase.DELETING:
                raise ValueError("deletion job is not active")
            document_id = job.document_id
            rows = self._remaining_chunk_rows(document_id)
            generation_id = self.id_factory()
            passages = tuple(
                self.input_builder.passage(
                    str(row["text"]),
                    filename=str(row["display_name"]),
                    location=_location_from_json(str(row["location_json"])),
                )
                for row in rows
            )
            embeddings = await self.embedder.embed_passages(passages)
            keys = tuple(int(row["chunk_key"]) for row in rows)
            self.vectors.set_generation_members(generation_id, keys)
            self.lexical.rebuild_generation(generation_id)
            self.vectors.build_generation(
                generation_id,
                str(self.embedder.profile_id),
                dict(zip(keys, embeddings, strict=True)),
            )
            self._remove_original_directory(document_id)
            self.vectors.activate_generation(
                generation_id,
                str(self.embedder.profile_id),
                transaction_callback=lambda connection: self._purge_document(
                    connection,
                    job_id,
                    document_id,
                ),
            )
            completed = self.jobs.get(job_id)
            await _notify_phase(on_phase, JobPhase.READY, document_id, None)
            return completed
        except Exception:
            if generation_id is not None:
                try:
                    self.vectors.discard_generation(generation_id)
                except Exception:
                    pass
            current = self.jobs.get(job_id)
            if current.phase not in {JobPhase.READY, JobPhase.FAILED}:
                self.jobs.increment_attempts(job_id)
            failed = self.jobs.get(job_id)
            await _notify_phase(
                on_phase,
                failed.phase,
                failed.document_id,
                failed.error_code or RagErrorCode.INDEXING_FAILED,
            )
            return failed

    def _remaining_chunk_rows(self, document_id: DocumentId) -> tuple[sqlite3.Row, ...]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT c.chunk_key, c.text, c.location_json, d.display_name "
                "FROM chunks AS c JOIN documents AS d ON d.document_id = c.document_id "
                "WHERE d.status = 'ready' AND d.document_id != ? "
                "AND c.embedding_profile_id = ? ORDER BY c.chunk_key",
                (str(document_id), str(self.embedder.profile_id)),
            ).fetchall()
        return tuple(rows)

    def _purge_document(
        self,
        connection: sqlite3.Connection,
        job_id: JobId,
        document_id: DocumentId,
    ) -> None:
        document = connection.execute(
            "SELECT original_bytes FROM documents "
            "WHERE document_id = ? AND status = 'deleting'",
            (str(document_id),),
        ).fetchone()
        if document is None:
            raise ValueError("deleting document is missing")
        chunk_rows = connection.execute(
            "SELECT chunk_key FROM chunks WHERE document_id = ?",
            (str(document_id),),
        ).fetchall()
        connection.executemany(
            "DELETE FROM chunks_fts WHERE rowid = ?",
            ((int(row["chunk_key"]),) for row in chunk_rows),
        )
        connection.execute(
            "UPDATE jobs SET document_id = NULL WHERE document_id = ?",
            (str(document_id),),
        )
        connection.execute("DELETE FROM chunks WHERE document_id = ?", (str(document_id),))
        connection.execute("DELETE FROM documents WHERE document_id = ?", (str(document_id),))
        connection.execute(
            "UPDATE quota_ledger SET committed_bytes = committed_bytes - ? "
            "WHERE singleton = 1",
            (int(document["original_bytes"]),),
        )
        timestamp = int(self.clock().timestamp())
        connection.execute(
            "UPDATE jobs SET phase = 'ready', document_id = NULL, error_code = NULL, "
            "updated_at = ? WHERE job_id = ?",
            (timestamp, str(job_id)),
        )

    def _remove_original_directory(self, document_id: DocumentId) -> None:
        directory = self.store.paths.originals / str(document_id)
        if not directory.exists():
            return
        if directory.is_symlink() or directory.parent != self.store.paths.originals:
            raise RuntimeError("refusing to remove unsafe managed original directory")
        shutil.rmtree(directory)

    @staticmethod
    def _authorize(context: RagRequestContext) -> None:
        if (
            not context.authenticated_sender
            or context.conversation_scope is not ConversationScope.PRIVATE
        ):
            raise PermissionError("private authenticated RAG context required")


def _location_from_json(value: str) -> SourceLocation:
    payload = json.loads(value)
    return SourceLocation(
        kind=SourceKind(str(payload["kind"])),
        page=payload.get("page"),
        heading_path=tuple(payload.get("heading_path", ())),
        slide=payload.get("slide"),
        sheet=payload.get("sheet"),
        row_start=payload.get("row_start"),
        row_end=payload.get("row_end"),
        line_start=payload.get("line_start"),
        line_end=payload.get("line_end"),
    )


async def _notify_phase(
    callback: JobPhaseCallback | None,
    phase: JobPhase,
    document_id: DocumentId | None,
    error_code: RagErrorCode | None,
) -> None:
    if callback is None:
        return
    try:
        await callback(phase, document_id, error_code)
    except Exception:
        pass


__all__ = ["RagDeletionService"]
