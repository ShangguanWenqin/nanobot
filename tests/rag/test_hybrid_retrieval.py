from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

import pytest

from nanobot.rag.config import RagRetrievalConfig
from nanobot.rag.retrieval import (
    DenseCandidate,
    HybridRetriever,
    SqliteCandidateLoader,
    reciprocal_rank_fusion,
)
from nanobot.rag.store import RagStore
from nanobot.rag.types import (
    ChunkKey,
    DocumentId,
    EmbeddingProfileId,
    PrincipalId,
    RagErrorCode,
    RerankerProfileId,
    SearchStatus,
    SourceKind,
    SourceLocation,
)
from nanobot.rag.vector_store import VectorConsistencyError, VectorMatch


def _candidate(key: int, document: str = "a", text: str | None = None) -> DenseCandidate:
    return DenseCandidate(
        chunk_key=ChunkKey(key),
        document_id=DocumentId(document * 32),
        filename=f"{document}.txt",
        text=text or f"chunk {key}",
        location=SourceLocation(kind=SourceKind.TEXT_LINES, line_start=key, line_end=key),
    )


def test_rrf_fuses_two_rankings_deduplicates_and_uses_stable_tiebreak() -> None:
    candidates = {key: _candidate(key) for key in (1, 2, 3)}

    fused = reciprocal_rank_fusion(
        lexical=(1, 2),
        dense=(2, 3),
        candidates=candidates,
        rrf_k=60,
    )

    assert [item.candidate.chunk_key for item in fused] == [2, 1, 3]
    assert fused[0].fusion_score == pytest.approx(1 / 62 + 1 / 61)
    assert fused[1].fusion_score > fused[2].fusion_score


@dataclass
class FakeLexicalHit:
    chunk_key: int


class FakeLexical:
    def __init__(self, keys: tuple[int, ...]) -> None:
        self.keys = keys
        self.calls: list[tuple[str, str, int]] = []

    def search(self, query: str, *, generation_id: str, limit: int) -> tuple[FakeLexicalHit, ...]:
        self.calls.append((query, generation_id, limit))
        return tuple(FakeLexicalHit(key) for key in self.keys[:limit])


class FakePinned:
    generation_id = "1" * 32
    embedding_profile_id = "e5-v1"

    def __init__(self, keys: tuple[int, ...]) -> None:
        self.keys = keys
        self.calls: list[tuple[tuple[float, ...], int]] = []

    def search(self, query: tuple[float, ...], *, count: int) -> tuple[VectorMatch, ...]:
        self.calls.append((query, count))
        return tuple(VectorMatch(key, float(rank)) for rank, key in enumerate(self.keys[:count]))


class FakeVectors:
    def __init__(self, keys: tuple[int, ...], *, unavailable: bool = False) -> None:
        self.pinned = FakePinned(keys)
        self.unavailable = unavailable

    @contextmanager
    def pin_active(self):
        if self.unavailable:
            raise VectorConsistencyError("unavailable")
        yield self.pinned


class FakeEmbedder:
    profile_id = EmbeddingProfileId("e5-v1")
    dimension = 2

    async def embed_query(self, text: str) -> tuple[float, ...]:
        assert text == "query: question"
        return (1.0, 0.0)

    async def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        del texts
        return ()


class FakeReranker:
    profile_id = RerankerProfileId("bge-v1")

    async def score(self, query: str, passages: tuple[str, ...]) -> tuple[float, ...]:
        assert query == "question"
        return tuple(0.9 - index * 0.05 for index in range(len(passages)))


class FakeInputBuilder:
    def query(self, text: str) -> str:
        return f"query: {text}"

    def passage(self, text: str, *, filename: str, location: SourceLocation) -> str:
        del filename, location
        return text


def _retriever(
    *,
    lexical_keys: tuple[int, ...],
    dense_keys: tuple[int, ...],
    candidates: dict[int, DenseCandidate],
    vectors_unavailable: bool = False,
    allow_degraded: bool = True,
) -> tuple[HybridRetriever, FakeLexical, FakeVectors]:
    lexical = FakeLexical(lexical_keys)
    vectors = FakeVectors(dense_keys, unavailable=vectors_unavailable)
    retriever = HybridRetriever(
        config=RagRetrievalConfig(allow_lexical_degraded_mode=allow_degraded),
        lexical=lexical,
        vectors=vectors,
        embedder=FakeEmbedder(),
        reranker=FakeReranker(),
        input_builder=FakeInputBuilder(),  # type: ignore[arg-type]
        candidate_loader=lambda generation_id, keys: tuple(
            candidates[key] for key in keys if key in candidates
        ),
        active_generation=lambda: "1" * 32,
        acceptance_threshold=0.6,
    )
    return retriever, lexical, vectors


@pytest.mark.asyncio
async def test_hybrid_search_requests_default_limits_filters_missing_and_returns_evidence() -> None:
    candidates = {key: _candidate(key) for key in range(1, 51)}
    retriever, lexical, vectors = _retriever(
        lexical_keys=tuple(range(1, 46)),
        dense_keys=tuple(range(20, 66)),
        candidates=candidates,
    )

    result = await retriever.search("question")

    assert result.status is SearchStatus.EVIDENCE
    assert len(result.evidence) == 6
    assert lexical.calls == [("question", "1" * 32, 40)]
    assert vectors.pinned.calls == [((1.0, 0.0), 40)]
    assert all(int(item.chunk_key) <= 50 for item in result.evidence)


@pytest.mark.asyncio
async def test_reranking_applies_threshold_and_prioritizes_document_diversity() -> None:
    candidates = {
        1: _candidate(1, "a"),
        2: _candidate(2, "a"),
        3: _candidate(3, "b"),
        4: _candidate(4, "c"),
    }
    retriever, _, _ = _retriever(
        lexical_keys=(1, 2, 3, 4),
        dense_keys=(1, 2, 3, 4),
        candidates=candidates,
    )

    result = await retriever.search("question")

    assert result.status is SearchStatus.EVIDENCE
    assert [str(item.document_id)[0] for item in result.evidence[:3]] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_dense_failure_is_explicitly_degraded_or_unavailable_by_policy() -> None:
    candidates = {1: _candidate(1)}
    degraded, _, _ = _retriever(
        lexical_keys=(1,),
        dense_keys=(),
        candidates=candidates,
        vectors_unavailable=True,
    )
    strict, _, _ = _retriever(
        lexical_keys=(1,),
        dense_keys=(),
        candidates=candidates,
        vectors_unavailable=True,
        allow_degraded=False,
    )

    degraded_result = await degraded.search("question")
    strict_result = await strict.search("question")

    assert degraded_result.status is SearchStatus.LEXICAL_DEGRADED
    assert ("mode", "lexical_only") in degraded_result.diagnostics
    assert strict_result.status is SearchStatus.UNAVAILABLE
    assert strict_result.reason is RagErrorCode.DENSE_INDEX_UNAVAILABLE


@pytest.mark.asyncio
async def test_no_candidate_above_threshold_returns_typed_no_evidence() -> None:
    candidates = {1: _candidate(1)}
    retriever, _, _ = _retriever(
        lexical_keys=(1,), dense_keys=(1,), candidates=candidates
    )
    retriever.acceptance_threshold = 0.95

    result = await retriever.search("question")

    assert result.status is SearchStatus.NO_EVIDENCE
    assert result.evidence == ()


def test_sqlite_candidate_loader_enforces_ready_generation_and_profile(
    tmp_path,
) -> None:
    store = RagStore.open(tmp_path, PrincipalId("f" * 64))
    generation = "1" * 32
    with store.connect() as connection:
        for number, status in ((1, "ready"), (2, "processing")):
            connection.execute(
                "INSERT INTO documents "
                "(document_id, display_name, content_sha256, mime_type, original_bytes, "
                "status, created_at, updated_at) VALUES (?, ?, ?, 'text/plain', 1, ?, 1, 1)",
                (f"{number:032x}", f"{number}.txt", f"{number:064x}", status),
            )
            connection.execute(
                "INSERT INTO chunks "
                "(chunk_key, document_id, ordinal, text, token_count, location_json, "
                "embedding_profile_id, generation_id) VALUES (?, ?, 0, ?, 1, ?, ?, ?)",
                (
                    number,
                    f"{number:032x}",
                    f"text-{number}",
                    '{"kind":"text_lines","line_start":1,"line_end":1}',
                    "e5-v1" if number == 1 else "other-profile",
                    generation,
                ),
            )

    loaded = SqliteCandidateLoader(store, embedding_profile_id="e5-v1")(
        generation, (1, 2, 999)
    )

    assert [int(item.chunk_key) for item in loaded] == [1]
    assert loaded[0].filename == "1.txt"
