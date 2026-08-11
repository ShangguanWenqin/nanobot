"""Strict domain types shared by the local private RAG subsystem."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import NewType

from nanobot.bus.events import ConversationScope

PrincipalId = NewType("PrincipalId", str)
DocumentId = NewType("DocumentId", str)
JobId = NewType("JobId", str)
OperationId = NewType("OperationId", str)
ChunkKey = NewType("ChunkKey", int)
VectorGenerationId = NewType("VectorGenerationId", str)
EmbeddingProfileId = NewType("EmbeddingProfileId", str)
RerankerProfileId = NewType("RerankerProfileId", str)


class DocumentStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


class JobOperation(StrEnum):
    INGEST = "ingest"
    DELETE = "delete"


class JobPhase(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"


class RagErrorCode(StrEnum):
    DISABLED = "disabled"
    NON_PRIVATE_CONVERSATION = "non_private_conversation"
    UNTRUSTED_IDENTITY = "untrusted_identity"
    UNSUPPORTED_FORMAT = "unsupported_format"
    UNSAFE_DOCUMENT = "unsafe_document"
    ENCRYPTED_DOCUMENT = "encrypted_document"
    NO_EXTRACTABLE_TEXT = "no_extractable_text"
    QUOTA_EXCEEDED = "quota_exceeded"
    LOW_DISK = "low_disk"
    MODEL_MISSING = "model_missing"
    MODEL_INTEGRITY_FAILED = "model_integrity_failed"
    MODEL_INITIALIZATION_FAILED = "model_initialization_failed"
    PARSE_TIMEOUT = "parse_timeout"
    INDEXING_FAILED = "indexing_failed"
    DENSE_INDEX_UNAVAILABLE = "dense_index_unavailable"
    RETRY_EXHAUSTED = "retry_exhausted"
    INTERNAL_ERROR = "internal_error"


class SourceKind(StrEnum):
    PDF_PAGE = "pdf_page"
    HEADING = "heading"
    SLIDE = "slide"
    SPREADSHEET_ROWS = "spreadsheet_rows"
    TEXT_LINES = "text_lines"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """The most precise structured location retained from a source document."""

    kind: SourceKind
    page: int | None = None
    heading_path: tuple[str, ...] = ()
    slide: int | None = None
    sheet: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    line_start: int | None = None
    line_end: int | None = None

    def __post_init__(self) -> None:
        if self.page is not None and self.page < 1:
            raise ValueError("page must be one-based")
        if self.slide is not None and self.slide < 1:
            raise ValueError("slide must be one-based")
        self._validate_range("row", self.row_start, self.row_end)
        self._validate_range("line", self.line_start, self.line_end)

        if self.kind is SourceKind.PDF_PAGE and self.page is None:
            raise ValueError("PDF source location requires page")
        if self.kind is SourceKind.HEADING and not self.heading_path:
            raise ValueError("heading source location requires heading_path")
        if self.kind is SourceKind.SLIDE and self.slide is None:
            raise ValueError("slide source location requires slide")
        if self.kind is SourceKind.SPREADSHEET_ROWS:
            if not self.sheet or self.row_start is None:
                raise ValueError("spreadsheet row source location requires sheet and row range")
        if self.kind is SourceKind.TEXT_LINES and self.line_start is None:
            raise ValueError("text source location requires line range")

    @staticmethod
    def _validate_range(name: str, start: int | None, end: int | None) -> None:
        if (start is None) != (end is None):
            raise ValueError(f"{name}_start and {name}_end must be provided together")
        if start is None or end is None:
            return
        if start < 1 or end < start:
            raise ValueError(f"{name} range must be one-based and ordered")


@dataclass(frozen=True, slots=True)
class RagRequestContext:
    """Server-created identity and routing facts for one RAG operation."""

    principal_id: PrincipalId
    channel: str
    sender_id: str
    chat_id: str
    conversation_scope: ConversationScope
    authenticated_sender: bool
    routing_metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.channel.strip():
            raise ValueError("channel must not be empty")
        if not self.sender_id.strip():
            raise ValueError("sender_id must not be empty")
        if not self.chat_id.strip():
            raise ValueError("chat_id must not be empty")


@dataclass(frozen=True, slots=True)
class RagDocument:
    document_id: DocumentId
    filename: str
    mime_type: str
    original_bytes: int
    content_sha256: str
    status: DocumentStatus
    created_at: datetime
    updated_at: datetime
    error_code: RagErrorCode | None = None


@dataclass(frozen=True, slots=True)
class RagChunk:
    chunk_key: ChunkKey
    document_id: DocumentId
    ordinal: int
    text: str
    token_count: int
    location: SourceLocation
    embedding_profile_id: EmbeddingProfileId


@dataclass(frozen=True, slots=True)
class RagJob:
    job_id: JobId
    operation: JobOperation
    phase: JobPhase
    attempts: int
    created_at: datetime
    updated_at: datetime
    document_id: DocumentId | None = None
    error_code: RagErrorCode | None = None


@dataclass(frozen=True, slots=True)
class RagEvidence:
    chunk_key: ChunkKey
    document_id: DocumentId
    filename: str
    text: str
    location: SourceLocation
    fusion_score: float
    reranker_score: float

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("evidence text must not be empty")
        if not math.isfinite(self.fusion_score) or not math.isfinite(self.reranker_score):
            raise ValueError("evidence scores must be finite")


class SearchStatus(StrEnum):
    EVIDENCE = "evidence"
    NO_EVIDENCE = "no_evidence"
    UNAVAILABLE = "unavailable"
    LEXICAL_DEGRADED = "lexical_degraded"


@dataclass(frozen=True, slots=True)
class RagSearchResult:
    status: SearchStatus
    evidence: tuple[RagEvidence, ...] = ()
    reason: RagErrorCode | None = None
    diagnostics: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        evidence_states = {SearchStatus.EVIDENCE, SearchStatus.LEXICAL_DEGRADED}
        if self.status in evidence_states and not self.evidence:
            raise ValueError(f"search status {self.status.value} requires evidence")
        if self.status not in evidence_states and self.evidence:
            raise ValueError(f"evidence must be empty for search status {self.status.value}")


__all__ = [
    "ChunkKey",
    "ConversationScope",
    "DocumentId",
    "DocumentStatus",
    "EmbeddingProfileId",
    "JobId",
    "JobOperation",
    "JobPhase",
    "OperationId",
    "PrincipalId",
    "RagChunk",
    "RagDocument",
    "RagErrorCode",
    "RagEvidence",
    "RagJob",
    "RagRequestContext",
    "RagSearchResult",
    "RerankerProfileId",
    "SearchStatus",
    "SourceKind",
    "SourceLocation",
    "VectorGenerationId",
]
