from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nanobot.rag.protocols import Clock, DiskProbe, Embedder, Reranker
from nanobot.rag.types import (
    ChunkKey,
    ConversationScope,
    DocumentId,
    DocumentStatus,
    EmbeddingProfileId,
    JobId,
    JobPhase,
    PrincipalId,
    RagErrorCode,
    RagEvidence,
    RagRequestContext,
    RagSearchResult,
    RerankerProfileId,
    SearchStatus,
    SourceKind,
    SourceLocation,
)


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 11, tzinfo=UTC)


class FakeDiskProbe:
    def free_bytes(self, path: Path) -> int:
        return 1024

    def used_bytes(self, path: Path) -> int:
        return 512


class FakeEmbedder:
    profile_id = EmbeddingProfileId("embedding-v1")
    dimension = 2

    async def embed_query(self, text: str) -> tuple[float, ...]:
        return (1.0, 0.0)

    async def embed_passages(
        self, texts: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        return tuple((0.0, 1.0) for _ in texts)


class FakeReranker:
    profile_id = RerankerProfileId("reranker-v1")

    async def score(
        self, query: str, passages: tuple[str, ...]
    ) -> tuple[float, ...]:
        return tuple(0.5 for _ in passages)


def test_request_context_is_immutable_and_principal_is_server_derived() -> None:
    context = RagRequestContext(
        principal_id=PrincipalId("principal"),
        channel="telegram",
        sender_id="42",
        chat_id="private-chat",
        conversation_scope=ConversationScope.PRIVATE,
        authenticated_sender=True,
        routing_metadata=(("reply_to", "message-1"),),
    )

    with pytest.raises(FrozenInstanceError):
        context.sender_id = "attacker"  # type: ignore[misc]

    assert context.routing_metadata == (("reply_to", "message-1"),)


def test_source_locations_validate_one_based_coordinates() -> None:
    location = SourceLocation(kind=SourceKind.PDF_PAGE, page=1)

    assert location.page == 1
    with pytest.raises(ValueError, match="page"):
        SourceLocation(kind=SourceKind.PDF_PAGE, page=0)
    with pytest.raises(ValueError, match="row"):
        SourceLocation(kind=SourceKind.SPREADSHEET_ROWS, sheet="Data", row_start=3)


def test_search_result_enforces_typed_evidence_states() -> None:
    evidence = RagEvidence(
        chunk_key=ChunkKey(1),
        document_id=DocumentId("doc-1"),
        filename="guide.pdf",
        text="可信证据",
        location=SourceLocation(kind=SourceKind.PDF_PAGE, page=2),
        fusion_score=0.2,
        reranker_score=0.8,
    )

    result = RagSearchResult(status=SearchStatus.EVIDENCE, evidence=(evidence,))
    assert result.evidence == (evidence,)

    with pytest.raises(ValueError, match="evidence"):
        RagSearchResult(status=SearchStatus.EVIDENCE)
    with pytest.raises(ValueError, match="must be empty"):
        RagSearchResult(status=SearchStatus.NO_EVIDENCE, evidence=(evidence,))


def test_domain_states_and_error_codes_are_stable_strings() -> None:
    assert DocumentStatus.READY.value == "ready"
    assert JobPhase.EMBEDDING.value == "embedding"
    assert JobPhase.DELETING.value == "deleting"
    assert RagErrorCode.QUOTA_EXCEEDED.value == "quota_exceeded"
    assert JobId("job-1") == "job-1"


def test_fake_dependencies_satisfy_runtime_protocols() -> None:
    assert isinstance(FakeClock(), Clock)
    assert isinstance(FakeDiskProbe(), DiskProbe)
    assert isinstance(FakeEmbedder(), Embedder)
    assert isinstance(FakeReranker(), Reranker)
