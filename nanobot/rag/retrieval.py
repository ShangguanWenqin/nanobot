"""Deterministic hybrid retrieval, RRF fusion, local reranking, and evidence policy."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import ContextManager, Protocol, TypeVar

from nanobot.rag.chunking import EmbeddingInputBuilder
from nanobot.rag.config import RagRetrievalConfig
from nanobot.rag.inference_scheduler import PriorityInferenceScheduler
from nanobot.rag.protocols import Embedder, Reranker
from nanobot.rag.store import RagStore
from nanobot.rag.types import (
    ChunkKey,
    DocumentId,
    RagErrorCode,
    RagEvidence,
    RagSearchResult,
    SearchStatus,
    SourceKind,
    SourceLocation,
)
from nanobot.rag.vector_store import PinnedVectorGeneration, VectorConsistencyError


@dataclass(frozen=True, slots=True)
class DenseCandidate:
    """Original evidence metadata loaded locally for one retrieved chunk key."""

    chunk_key: ChunkKey
    document_id: DocumentId
    filename: str
    text: str
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    candidate: DenseCandidate
    fusion_score: float


class LexicalHitLike(Protocol):
    chunk_key: int


class LexicalSearch(Protocol):
    def search(
        self,
        query: str,
        *,
        generation_id: str,
        limit: int,
    ) -> Sequence[LexicalHitLike]: ...


class VectorSearch(Protocol):
    def pin_active(self) -> ContextManager[PinnedVectorGeneration]: ...


CandidateLoader = Callable[[str, tuple[int, ...]], Sequence[DenseCandidate]]
T = TypeVar("T")


class SqliteCandidateLoader:
    def __init__(self, store: RagStore, *, embedding_profile_id: str) -> None:
        self.store = store
        self.embedding_profile_id = embedding_profile_id

    def __call__(
        self,
        generation_id: str,
        keys: tuple[int, ...],
    ) -> tuple[DenseCandidate, ...]:
        if not keys:
            return ()
        placeholders = ",".join("?" for _ in keys)
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT c.chunk_key, c.document_id, d.display_name, c.text, c.location_json "
                "FROM chunks AS c "
                "JOIN generation_chunks AS gc ON gc.chunk_key = c.chunk_key "
                "JOIN documents AS d ON d.document_id = c.document_id "
                f"WHERE c.chunk_key IN ({placeholders}) AND gc.generation_id = ? "
                "AND c.embedding_profile_id = ? AND d.status = 'ready'",
                (*keys, generation_id, self.embedding_profile_id),
            ).fetchall()
        by_key = {
            int(row["chunk_key"]): DenseCandidate(
                chunk_key=ChunkKey(int(row["chunk_key"])),
                document_id=DocumentId(str(row["document_id"])),
                filename=str(row["display_name"]),
                text=str(row["text"]),
                location=_location_from_json(str(row["location_json"])),
            )
            for row in rows
        }
        return tuple(by_key[key] for key in keys if key in by_key)


def reciprocal_rank_fusion(
    *,
    lexical: Sequence[int],
    dense: Sequence[int],
    candidates: dict[int, DenseCandidate],
    rrf_k: int,
) -> tuple[RankedCandidate, ...]:
    if rrf_k < 1:
        raise ValueError("RRF k must be positive")
    scores: dict[int, float] = {}
    for ranking in (lexical, dense):
        seen: set[int] = set()
        for rank, key in enumerate(ranking, start=1):
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
    return tuple(
        RankedCandidate(candidates[key], score)
        for key, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        if key in candidates
    )


class HybridRetriever:
    def __init__(
        self,
        *,
        config: RagRetrievalConfig,
        lexical: LexicalSearch,
        vectors: VectorSearch,
        embedder: Embedder,
        reranker: Reranker,
        input_builder: EmbeddingInputBuilder,
        candidate_loader: CandidateLoader,
        active_generation: Callable[[], str],
        acceptance_threshold: float,
        inference_scheduler: PriorityInferenceScheduler | None = None,
    ) -> None:
        if not 0.0 <= acceptance_threshold <= 1.0:
            raise ValueError("reranker acceptance threshold must be between zero and one")
        self.config = config
        self.lexical = lexical
        self.vectors = vectors
        self.embedder = embedder
        self.reranker = reranker
        self.input_builder = input_builder
        self.candidate_loader = candidate_loader
        self.active_generation = active_generation
        self.acceptance_threshold = acceptance_threshold
        self.inference_scheduler = inference_scheduler

    async def search(self, query: str) -> RagSearchResult:
        if not query.strip():
            raise ValueError("RAG query must not be empty")
        generation_id = self.active_generation()
        lexical_hits = self.lexical.search(
            query,
            generation_id=generation_id,
            limit=self.config.lexical_candidates,
        )
        lexical_keys = tuple(int(hit.chunk_key) for hit in lexical_hits)
        dense_keys: tuple[int, ...] = ()
        degraded = False
        try:
            query_input = self.input_builder.query(query)
            query_vector = await self._interactive(
                lambda: self.embedder.embed_query(query_input)
            )
            with self.vectors.pin_active() as pinned:
                if (
                    pinned.generation_id != generation_id
                    or pinned.embedding_profile_id != str(self.embedder.profile_id)
                ):
                    raise VectorConsistencyError("active dense generation changed or is incompatible")
                dense_keys = tuple(
                    match.chunk_key
                    for match in pinned.search(
                        query_vector,
                        count=self.config.dense_candidates,
                    )
                )
        except Exception as exc:
            if not _is_dense_unavailable(exc):
                raise
            if not self.config.allow_lexical_degraded_mode:
                return RagSearchResult(
                    status=SearchStatus.UNAVAILABLE,
                    reason=RagErrorCode.DENSE_INDEX_UNAVAILABLE,
                )
            degraded = True

        ordered_keys = tuple(dict.fromkeys((*lexical_keys, *dense_keys)))
        loaded = self.candidate_loader(generation_id, ordered_keys)
        candidates = {int(item.chunk_key): item for item in loaded}
        fused = reciprocal_rank_fusion(
            lexical=lexical_keys,
            dense=dense_keys,
            candidates=candidates,
            rrf_k=self.config.rrf_k,
        )
        rerank_input = fused[: self.config.rerank_candidates]
        if not rerank_input:
            return self._empty_result(degraded)
        passages = tuple(
            self.input_builder.passage(
                item.candidate.text,
                filename=item.candidate.filename,
                location=item.candidate.location,
            )
            for item in rerank_input
        )
        scores = await self._interactive(lambda: self.reranker.score(query, passages))
        if len(scores) != len(rerank_input):
            raise ValueError("reranker result count does not match candidates")
        accepted = sorted(
            (
                (item, score)
                for item, score in zip(rerank_input, scores, strict=True)
                if score >= self.acceptance_threshold
            ),
            key=lambda item: (-item[1], -item[0].fusion_score, int(item[0].candidate.chunk_key)),
        )
        diverse = _document_diverse(accepted, self.config.max_evidence)
        if not diverse:
            return self._empty_result(degraded)
        status = SearchStatus.LEXICAL_DEGRADED if degraded else SearchStatus.EVIDENCE
        diagnostics = (("mode", "lexical_only"),) if degraded else ()
        return RagSearchResult(
            status=status,
            evidence=tuple(
                RagEvidence(
                    chunk_key=item.candidate.chunk_key,
                    document_id=item.candidate.document_id,
                    filename=item.candidate.filename,
                    text=item.candidate.text,
                    location=item.candidate.location,
                    fusion_score=item.fusion_score,
                    reranker_score=score,
                )
                for item, score in diverse
            ),
            diagnostics=diagnostics,
        )

    async def _interactive(self, function: Callable[[], Awaitable[T]]) -> T:
        if self.inference_scheduler is None:
            return await function()
        return await self.inference_scheduler.run_interactive(function)

    @staticmethod
    def _empty_result(degraded: bool) -> RagSearchResult:
        return RagSearchResult(
            status=SearchStatus.NO_EVIDENCE,
            diagnostics=(("mode", "lexical_only"),) if degraded else (),
        )


def _document_diverse(
    accepted: Sequence[tuple[RankedCandidate, float]],
    limit: int,
) -> tuple[tuple[RankedCandidate, float], ...]:
    first: list[tuple[RankedCandidate, float]] = []
    later: list[tuple[RankedCandidate, float]] = []
    seen_documents: set[DocumentId] = set()
    for item in accepted:
        document_id = item[0].candidate.document_id
        if document_id in seen_documents:
            later.append(item)
        else:
            seen_documents.add(document_id)
            first.append(item)
    return tuple((*first, *later)[:limit])


def _is_dense_unavailable(error: Exception) -> bool:
    return isinstance(error, (VectorConsistencyError, RuntimeError, ValueError))


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
    "DenseCandidate",
    "HybridRetriever",
    "RankedCandidate",
    "SqliteCandidateLoader",
    "reciprocal_rank_fusion",
]
