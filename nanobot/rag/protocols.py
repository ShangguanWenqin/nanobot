"""Narrow injectable dependencies for deterministic RAG tests and runtimes."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from nanobot.rag.types import EmbeddingProfileId, RerankerProfileId


@runtime_checkable
class LifecycleComponent(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class DiskProbe(Protocol):
    def free_bytes(self, path: Path) -> int: ...

    def used_bytes(self, path: Path) -> int: ...


@runtime_checkable
class Embedder(Protocol):
    profile_id: EmbeddingProfileId
    dimension: int

    async def embed_query(self, text: str) -> tuple[float, ...]: ...

    async def embed_passages(
        self, texts: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]: ...


@runtime_checkable
class Reranker(Protocol):
    profile_id: RerankerProfileId

    async def score(
        self, query: str, passages: tuple[str, ...]
    ) -> tuple[float, ...]: ...


__all__ = ["Clock", "DiskProbe", "Embedder", "LifecycleComponent", "Reranker"]
