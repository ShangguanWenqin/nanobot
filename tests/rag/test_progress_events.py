from __future__ import annotations

import pytest

from nanobot.rag.progress import (
    RagOperation,
    RagPhase,
    RagProgressEvent,
    RagProgressPublisher,
    RagProgressState,
)
from nanobot.rag.types import DocumentId, OperationId, RagErrorCode


def _event(**overrides) -> RagProgressEvent:
    values = {
        "operation_id": OperationId("a" * 32),
        "operation": RagOperation.QUERY,
        "phase": RagPhase.QUERYING,
        "state": RagProgressState.RUNNING,
        "fallback_text": "正在从 RAG 知识库中查询…",
    }
    values.update(overrides)
    return RagProgressEvent(**values)


def test_progress_event_serializes_only_safe_bounded_fields() -> None:
    event = _event(
        current=2,
        total=5,
        document_id=DocumentId("b" * 32),
        filename="/Users/alice/private/guide.pdf",
        error_code=RagErrorCode.MODEL_MISSING,
        fallback_text="失败位置 /Users/alice/private/model.onnx",
    )

    payload = event.to_public_dict()
    serialized = str(payload)

    assert payload["kind"] == "rag_progress"
    assert payload["filename"] == "guide.pdf"
    assert payload["current"] == 2 and payload["total"] == 5
    assert "/Users/" not in serialized
    assert "document_text" not in serialized
    assert "evidence" not in serialized
    assert "embedding" not in serialized.lower() or payload["phase"] == "embedding"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"operation_id": OperationId("../victim")}, "operation_id"),
        ({"phase": RagPhase.PARSING}, "phase"),
        ({"current": 6, "total": 5}, "current"),
        ({"current": 1, "total": None}, "together"),
        ({"state": RagProgressState.FAILED, "error_code": None}, "error_code"),
    ],
)
def test_progress_event_rejects_invalid_operation_phase_state_combinations(
    overrides,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _event(**overrides)


@pytest.mark.asyncio
async def test_progress_publisher_deduplicates_and_throttles_nonterminal_updates() -> None:
    delivered: list[RagProgressEvent] = []
    now = [0.0]

    async def deliver(event: RagProgressEvent) -> None:
        delivered.append(event)

    publisher = RagProgressPublisher(deliver, min_interval_seconds=1.0, clock=lambda: now[0])
    running = _event()

    assert await publisher.publish(running) is True
    assert await publisher.publish(running) is False
    now[0] = 0.5
    assert await publisher.publish(_event(phase=RagPhase.RERANKING)) is False
    completed = _event(
        phase=RagPhase.COMPLETED,
        state=RagProgressState.COMPLETED,
        fallback_text="RAG 查询完成。",
    )
    assert await publisher.publish(completed) is True
    assert delivered == [running, completed]


@pytest.mark.asyncio
async def test_progress_delivery_failure_is_best_effort() -> None:
    async def fail(_event: RagProgressEvent) -> None:
        raise RuntimeError("contains /Users/alice/private/file and document text")

    publisher = RagProgressPublisher(fail, min_interval_seconds=0)

    assert await publisher.publish(_event()) is False
