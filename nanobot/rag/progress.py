"""Typed, privacy-safe, best-effort RAG progress events."""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePath

from loguru import logger

from nanobot.bus.outbound_events import OutboundEvent
from nanobot.rag.types import DocumentId, OperationId, RagErrorCode

_SYSTEM_ID = re.compile(r"[0-9a-f]{32}")


class RagOperation(StrEnum):
    INGEST = "ingest"
    QUERY = "query"
    DELETE = "delete"
    MODEL_PREPARE = "model_prepare"
    BENCHMARK = "benchmark"


class RagPhase(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    QUERYING = "querying"
    FUSING = "fusing"
    RERANKING = "reranking"
    DELETING = "deleting"
    DOWNLOADING = "downloading"
    BENCHMARKING = "benchmarking"
    SELECTED = "selected"
    DEGRADED = "degraded"
    FALLBACK = "fallback"
    COMPLETED = "completed"
    FAILED = "failed"


class RagProgressState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


_PHASES_BY_OPERATION: dict[RagOperation, frozenset[RagPhase]] = {
    RagOperation.INGEST: frozenset(
        {
            RagPhase.QUEUED,
            RagPhase.PARSING,
            RagPhase.CHUNKING,
            RagPhase.EMBEDDING,
            RagPhase.INDEXING,
            RagPhase.COMPLETED,
            RagPhase.FAILED,
        }
    ),
    RagOperation.QUERY: frozenset(
        {
            RagPhase.QUERYING,
            RagPhase.FUSING,
            RagPhase.RERANKING,
            RagPhase.DEGRADED,
            RagPhase.FALLBACK,
            RagPhase.COMPLETED,
            RagPhase.FAILED,
        }
    ),
    RagOperation.DELETE: frozenset(
        {RagPhase.QUEUED, RagPhase.DELETING, RagPhase.COMPLETED, RagPhase.FAILED}
    ),
    RagOperation.MODEL_PREPARE: frozenset(
        {RagPhase.DOWNLOADING, RagPhase.COMPLETED, RagPhase.FAILED}
    ),
    RagOperation.BENCHMARK: frozenset(
        {
            RagPhase.BENCHMARKING,
            RagPhase.SELECTED,
            RagPhase.FALLBACK,
            RagPhase.COMPLETED,
            RagPhase.FAILED,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class RagProgressEvent(OutboundEvent):
    operation_id: OperationId
    operation: RagOperation
    phase: RagPhase
    state: RagProgressState
    fallback_text: str
    current: int | None = None
    total: int | None = None
    document_id: DocumentId | None = None
    filename: str | None = None
    error_code: RagErrorCode | None = None

    def __post_init__(self) -> None:
        if _SYSTEM_ID.fullmatch(str(self.operation_id)) is None:
            raise ValueError("operation_id must be a 32-character lowercase hex system ID")
        if self.phase not in _PHASES_BY_OPERATION[self.operation]:
            raise ValueError(f"phase {self.phase.value} is invalid for {self.operation.value}")
        if (self.current is None) != (self.total is None):
            raise ValueError("current and total must be provided together")
        if self.current is not None and self.total is not None:
            if self.current < 0 or self.total < 1 or self.current > self.total:
                raise ValueError("current must be between zero and total")
        if self.state is RagProgressState.FAILED and self.error_code is None:
            raise ValueError("failed progress requires error_code")
        if self.state is RagProgressState.COMPLETED and self.phase is not RagPhase.COMPLETED:
            raise ValueError("completed state requires completed phase")
        if not self.fallback_text.strip():
            raise ValueError("fallback_text must not be empty")

    @property
    def deduplication_key(self) -> tuple[OperationId, RagPhase, RagProgressState]:
        return (self.operation_id, self.phase, self.state)

    def to_public_dict(self) -> dict[str, str | int]:
        payload: dict[str, str | int] = {
            "kind": "rag_progress",
            "operation_id": str(self.operation_id),
            "operation": self.operation.value,
            "phase": self.phase.value,
            "state": self.state.value,
            "fallback_text": _safe_text(self.fallback_text),
        }
        if self.current is not None and self.total is not None:
            payload.update(current=self.current, total=self.total)
        if self.document_id is not None:
            payload["document_id"] = str(self.document_id)
        if self.filename:
            payload["filename"] = PurePath(self.filename.replace("\\", "/")).name[:255]
        if self.error_code is not None:
            payload["error_code"] = self.error_code.value
        return payload


ProgressDelivery = Callable[[RagProgressEvent], Awaitable[None]]


class RagProgressPublisher:
    """Deduplicate/throttle events while keeping delivery non-authoritative."""

    def __init__(
        self,
        deliver: ProgressDelivery,
        *,
        min_interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must not be negative")
        self._deliver = deliver
        self._min_interval = min_interval_seconds
        self._clock = clock
        self._seen: set[tuple[OperationId, RagPhase, RagProgressState]] = set()
        self._last_published_at: dict[OperationId, float] = {}

    async def publish(self, event: RagProgressEvent) -> bool:
        key = event.deduplication_key
        if key in self._seen:
            return False
        now = self._clock()
        last = self._last_published_at.get(event.operation_id)
        terminal = event.state in {RagProgressState.COMPLETED, RagProgressState.FAILED}
        if last is not None and not terminal and now - last < self._min_interval:
            return False
        try:
            await self._deliver(event)
        except Exception:
            logger.warning(
                "RAG progress delivery failed: operation={} phase={} state={}",
                event.operation.value,
                event.phase.value,
                event.state.value,
            )
            return False
        self._seen.add(key)
        self._last_published_at[event.operation_id] = now
        return True


def _safe_text(value: str) -> str:
    """Return a bounded fallback without echoing likely host paths."""
    words = value.replace("\\", "/").split()
    sanitized = ["[路径已隐藏]" if "/" in word else word for word in words]
    return " ".join(sanitized)[:500] or "RAG 操作状态已更新。"


__all__ = [
    "RagOperation",
    "RagPhase",
    "RagProgressEvent",
    "RagProgressPublisher",
    "RagProgressState",
]
