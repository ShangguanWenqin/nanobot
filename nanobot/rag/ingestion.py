"""Persistent, all-or-none acceptance and publication of private RAG documents."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from nanobot.bus.events import ConversationScope
from nanobot.rag.chunking import DeterministicChunker, EmbeddingInputBuilder
from nanobot.rag.config import RagParsingConfig
from nanobot.rag.documents import AcceptedOriginal, DocumentRepository
from nanobot.rag.inference_scheduler import PriorityInferenceScheduler
from nanobot.rag.job_state import JobRepository
from nanobot.rag.lexical import LexicalRepository
from nanobot.rag.parser import (
    ParseCompleteness,
    ParsedDocument,
    RagParseError,
    detect_document_format,
)
from nanobot.rag.protocols import Embedder
from nanobot.rag.quota import QuotaManager
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


class DocumentParser(Protocol):
    async def __call__(self, path: Path) -> ParsedDocument: ...


@dataclass(frozen=True, slots=True)
class IngestionAttachment:
    source_path: Path
    display_name: str
    mime_type: str


@dataclass(frozen=True, slots=True)
class AcceptedIngestion:
    document_id: DocumentId
    job_id: JobId | None
    duplicate: bool


@dataclass(frozen=True, slots=True)
class AcceptedIngestionBatch:
    items: tuple[AcceptedIngestion, ...]


@dataclass(frozen=True, slots=True)
class _PendingOriginal:
    attachment: IngestionAttachment
    reservation_id: str
    document_id: DocumentId
    job_id: JobId
    byte_count: int


class RagIngestionService:
    """Accept uploads quickly, then run their expensive local pipeline asynchronously."""

    def __init__(
        self,
        *,
        store: RagStore,
        quota: QuotaManager,
        parser: DocumentParser,
        parsing_config: RagParsingConfig,
        chunker: DeterministicChunker,
        input_builder: EmbeddingInputBuilder,
        embedder: Embedder,
        vectors: VectorGenerationRepository,
        lexical: LexicalRepository,
        clock: Callable[[], datetime],
        id_factory: Callable[[], str],
        inference_scheduler: PriorityInferenceScheduler | None = None,
        embedding_batch_size: int = 32,
    ) -> None:
        if embedding_batch_size < 1:
            raise ValueError("embedding batch size must be positive")
        self.store = store
        self.quota = quota
        self.parser = parser
        self.parsing_config = parsing_config
        self.chunker = chunker
        self.input_builder = input_builder
        self.embedder = embedder
        self.vectors = vectors
        self.lexical = lexical
        self.clock = clock
        self.id_factory = id_factory
        self.inference_scheduler = inference_scheduler
        self.embedding_batch_size = embedding_batch_size
        self.documents = DocumentRepository(store)
        self.jobs = JobRepository(store, clock=clock)

    async def accept_batch(
        self,
        context: RagRequestContext,
        attachments: Sequence[IngestionAttachment],
    ) -> AcceptedIngestionBatch:
        self._authorize(context)
        pending = self._preflight(attachments)
        self.quota.reserve_batch(
            tuple((item.reservation_id, item.byte_count) for item in pending)
        )
        accepted: list[tuple[_PendingOriginal, AcceptedOriginal]] = []
        try:
            for item in pending:
                original = await asyncio.to_thread(
                    self.documents.accept_original,
                    item.attachment.source_path,
                    document_id=str(item.document_id),
                    job_id=str(item.job_id),
                    reservation_id=item.reservation_id,
                    display_name=item.attachment.display_name,
                    mime_type=item.attachment.mime_type,
                    defer_quota_commit=True,
                )
                accepted.append((item, original))
                if original.duplicate:
                    continue
                self.jobs.create(
                    job_id=item.job_id,
                    operation=JobOperation.INGEST,
                    channel=context.channel,
                    chat_id=context.chat_id,
                    document_id=original.document_id,
                    reservation_id=item.reservation_id,
                )
                with self.store.connect() as connection:
                    connection.execute(
                        "UPDATE quota_reservations SET job_id = ? WHERE reservation_id = ?",
                        (str(item.job_id), item.reservation_id),
                    )
        except BaseException:
            self._rollback_batch(pending, accepted)
            raise
        return AcceptedIngestionBatch(
            tuple(
                AcceptedIngestion(
                    document_id=original.document_id,
                    job_id=None if original.duplicate else item.job_id,
                    duplicate=original.duplicate,
                )
                for item, original in accepted
            )
        )

    async def process_job(self, job_id: JobId) -> RagJob:
        generation_id: str | None = None
        try:
            job = self.jobs.get(job_id)
            if job.phase is not JobPhase.QUEUED or job.document_id is None:
                raise ValueError("ingestion job is not queued with a document")
            document_id = job.document_id
            document = self._document_row(document_id)
            managed_path = self.store.paths.principal_root / str(document["original_relpath"])

            self.jobs.transition(job_id, JobPhase.PARSING)
            parsed = await self.parser(managed_path)
            if parsed.completeness is not ParseCompleteness.COMPLETE:
                raise RagParseError(
                    RagErrorCode.UNSAFE_DOCUMENT,
                    "文档达到解析安全上限，未作为完整知识入库",
                )

            self.jobs.transition(job_id, JobPhase.CHUNKING)
            drafts = self.chunker.chunk(parsed.blocks)
            if not drafts:
                raise RagParseError(RagErrorCode.NO_EXTRACTABLE_TEXT, "文档中没有可提取文本")

            self.jobs.transition(job_id, JobPhase.EMBEDDING)
            generation_id = self.id_factory()
            current_rows = tuple(
                (
                    self._chunk_key(document_id, draft.ordinal),
                    str(document_id),
                    draft.ordinal,
                    draft.text,
                    draft.token_count,
                    _location_json(draft.location),
                    str(self.embedder.profile_id),
                    generation_id,
                )
                for draft in drafts
            )
            self._insert_chunks(current_rows)

            self.jobs.transition(job_id, JobPhase.INDEXING)
            rows = self._indexable_chunk_rows(document_id)
            passages = tuple(
                self.input_builder.passage(
                    str(row["text"]),
                    filename=str(row["display_name"]),
                    location=_location_from_json(str(row["location_json"])),
                )
                for row in rows
            )
            embeddings = await self._embed_passages(passages)
            if len(embeddings) != len(rows):
                raise ValueError("embedding result count does not match chunks")
            chunk_keys = tuple(int(row["chunk_key"]) for row in rows)
            self.vectors.set_generation_members(generation_id, chunk_keys)
            self.lexical.rebuild_generation(generation_id)
            self.vectors.build_generation(
                generation_id,
                str(self.embedder.profile_id),
                dict(zip(chunk_keys, embeddings, strict=True)),
            )
            self.vectors.activate_generation(
                generation_id,
                str(self.embedder.profile_id),
                transaction_callback=lambda connection: self._publish_ready(
                    connection,
                    job_id,
                    document_id,
                    generation_id,
                ),
            )
            return self.jobs.get(job_id)
        except RagParseError as exc:
            self._fail_job(job_id, exc.code, generation_id)
            return self.jobs.get(job_id)
        except Exception:
            self._fail_job(job_id, RagErrorCode.INDEXING_FAILED, generation_id)
            return self.jobs.get(job_id)

    def _preflight(
        self,
        attachments: Sequence[IngestionAttachment],
    ) -> tuple[_PendingOriginal, ...]:
        if not attachments:
            raise ValueError("ingestion batch must contain at least one attachment")
        if len(attachments) > self.parsing_config.max_attachments_per_batch:
            raise ValueError("ingestion batch contains too many attachments")
        pending: list[_PendingOriginal] = []
        for attachment in attachments:
            path = attachment.source_path
            if path.is_symlink() or not path.is_file():
                raise RagParseError(RagErrorCode.UNSAFE_DOCUMENT, "无法读取待入库文件")
            detect_document_format(path)
            byte_count = path.stat().st_size
            if byte_count < 1:
                raise RagParseError(RagErrorCode.NO_EXTRACTABLE_TEXT, "空文件无法入库")
            if byte_count > self.parsing_config.max_file_bytes:
                raise RagParseError(RagErrorCode.UNSAFE_DOCUMENT, "文件超过允许的大小上限")
            pending.append(
                _PendingOriginal(
                    attachment=attachment,
                    reservation_id=self.id_factory(),
                    document_id=DocumentId(self.id_factory()),
                    job_id=JobId(self.id_factory()),
                    byte_count=byte_count,
                )
            )
        return tuple(pending)

    async def _embed_passages(
        self,
        passages: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        scheduler = self.inference_scheduler
        if scheduler is None:
            return await self.embedder.embed_passages(passages)
        return await scheduler.run_background_batches(
            passages,
            batch_size=self.embedding_batch_size,
            handler=self.embedder.embed_passages,
        )

    @staticmethod
    def _authorize(context: RagRequestContext) -> None:
        if (
            not context.authenticated_sender
            or context.conversation_scope is not ConversationScope.PRIVATE
        ):
            raise PermissionError("private authenticated RAG context required")

    def _document_row(self, document_id: DocumentId) -> sqlite3.Row:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT document_id, display_name, original_bytes, original_relpath "
                "FROM documents WHERE document_id = ? AND status = 'processing'",
                (str(document_id),),
            ).fetchone()
        if row is None or row["original_relpath"] is None:
            raise ValueError("ingestion document is unavailable")
        return row

    def _insert_chunks(self, rows: tuple[tuple[object, ...], ...]) -> None:
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "INSERT INTO chunks "
                "(chunk_key, document_id, ordinal, text, token_count, location_json, "
                "embedding_profile_id, generation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def _indexable_chunk_rows(self, current_document_id: DocumentId) -> tuple[sqlite3.Row, ...]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT c.chunk_key, c.text, c.location_json, d.display_name "
                "FROM chunks AS c JOIN documents AS d ON d.document_id = c.document_id "
                "WHERE c.embedding_profile_id = ? AND "
                "(d.status = 'ready' OR d.document_id = ?) "
                "ORDER BY c.chunk_key",
                (str(self.embedder.profile_id), str(current_document_id)),
            ).fetchall()
        return tuple(rows)

    def _publish_ready(
        self,
        connection: sqlite3.Connection,
        job_id: JobId,
        document_id: DocumentId,
        generation_id: str,
    ) -> None:
        timestamp = int(self.clock().timestamp())
        reservation = connection.execute(
            "SELECT qr.reservation_id, qr.reserved_bytes FROM jobs AS j "
            "JOIN quota_reservations AS qr ON qr.reservation_id = j.reservation_id "
            "WHERE j.job_id = ? AND j.phase = 'indexing'",
            (str(job_id),),
        ).fetchone()
        if reservation is None:
            raise ValueError("ingestion reservation is missing")
        byte_count = int(reservation["reserved_bytes"])
        connection.execute(
            "UPDATE documents SET status = 'ready', generation_id = ?, updated_at = ?, "
            "error_code = NULL WHERE document_id = ?",
            (generation_id, timestamp, str(document_id)),
        )
        connection.execute(
            "UPDATE quota_ledger SET reserved_bytes = reserved_bytes - ?, "
            "committed_bytes = committed_bytes + ? WHERE singleton = 1",
            (byte_count, byte_count),
        )
        connection.execute(
            "DELETE FROM quota_reservations WHERE reservation_id = ?",
            (str(reservation["reservation_id"]),),
        )
        connection.execute(
            "UPDATE jobs SET phase = 'ready', error_code = NULL, updated_at = ? "
            "WHERE job_id = ?",
            (timestamp, str(job_id)),
        )

    def _fail_job(
        self,
        job_id: JobId,
        code: RagErrorCode,
        generation_id: str | None,
    ) -> None:
        try:
            job = self.jobs.get(job_id)
        except KeyError:
            return
        if generation_id is not None:
            try:
                self.vectors.discard_generation(generation_id)
            except Exception:
                pass
        if job.document_id is not None:
            self._remove_document(job.document_id, release_reservation=True)
        try:
            self.jobs.fail(job_id, code)
        except Exception:
            timestamp = int(self.clock().timestamp())
            with self.store.connect() as connection:
                connection.execute(
                    "UPDATE jobs SET phase = 'failed', error_code = ?, updated_at = ? "
                    "WHERE job_id = ?",
                    (code.value, timestamp, str(job_id)),
                )

    def _rollback_batch(
        self,
        pending: Sequence[_PendingOriginal],
        accepted: Sequence[tuple[_PendingOriginal, AcceptedOriginal]],
    ) -> None:
        for item, original in reversed(accepted):
            if not original.duplicate:
                self._remove_document(
                    item.document_id,
                    release_reservation=True,
                    remove_jobs=True,
                )
        for item in pending:
            self.quota.release_reservation(item.reservation_id)

    def _remove_document(
        self,
        document_id: DocumentId,
        *,
        release_reservation: bool,
        remove_jobs: bool = False,
    ) -> None:
        original_directory = self.store.paths.originals / str(document_id)
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reservation = connection.execute(
                "SELECT reservation_id, reserved_bytes, job_id FROM quota_reservations "
                "WHERE job_id IN (SELECT job_id FROM jobs WHERE document_id = ?)",
                (str(document_id),),
            ).fetchone()
            connection.execute(
                "UPDATE jobs SET document_id = NULL WHERE document_id = ?",
                (str(document_id),),
            )
            if remove_jobs:
                if reservation is not None and reservation["job_id"] is not None:
                    connection.execute(
                        "DELETE FROM jobs WHERE job_id = ?",
                        (str(reservation["job_id"]),),
                    )
            connection.execute("DELETE FROM chunks WHERE document_id = ?", (str(document_id),))
            connection.execute("DELETE FROM documents WHERE document_id = ?", (str(document_id),))
            if release_reservation and reservation is not None:
                byte_count = int(reservation["reserved_bytes"])
                connection.execute(
                    "UPDATE quota_ledger SET reserved_bytes = reserved_bytes - ? "
                    "WHERE singleton = 1",
                    (byte_count,),
                )
                connection.execute(
                    "DELETE FROM quota_reservations WHERE reservation_id = ?",
                    (str(reservation["reservation_id"]),),
                )
        if original_directory.exists():
            if original_directory.is_symlink() or original_directory.parent != self.store.paths.originals:
                raise RuntimeError("refusing to remove unsafe managed original directory")
            shutil.rmtree(original_directory)

    @staticmethod
    def _chunk_key(document_id: DocumentId, ordinal: int) -> int:
        digest = hashlib.sha256(
            b"nanobot-rag-chunk-v1\0" + str(document_id).encode() + b"\0" + str(ordinal).encode()
        ).digest()
        return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def _location_json(location: SourceLocation) -> str:
    payload = {
        "kind": location.kind.value,
        "page": location.page,
        "heading_path": list(location.heading_path),
        "slide": location.slide,
        "sheet": location.sheet,
        "row_start": location.row_start,
        "row_end": location.row_end,
        "line_start": location.line_start,
        "line_end": location.line_end,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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


__all__ = [
    "AcceptedIngestion",
    "AcceptedIngestionBatch",
    "DocumentParser",
    "IngestionAttachment",
    "RagIngestionService",
]
