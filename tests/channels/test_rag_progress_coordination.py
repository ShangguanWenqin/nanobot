from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.channels.manager import ChannelManager
from nanobot.config.schema import Config
from nanobot.rag.progress import (
    RagOperation,
    RagPhase,
    RagProgressEvent,
    RagProgressState,
)
from nanobot.rag.types import OperationId, RagErrorCode


def _event(
    operation: RagOperation,
    phase: RagPhase,
    state: RagProgressState,
) -> RagProgressEvent:
    return RagProgressEvent(
        operation_id=OperationId("a" * 32),
        operation=operation,
        phase=phase,
        state=state,
        error_code=RagErrorCode.INTERNAL_ERROR if state is RagProgressState.FAILED else None,
        fallback_text=f"{operation.value}:{phase.value}",
    )


class _Channel(BaseChannel):
    name = "mock"
    display_name = "Mock"

    def __init__(self, *, editable: bool) -> None:
        super().__init__({}, MessageBus())
        self.supports_progress_updates = editable
        self.sent = AsyncMock()
        self.updated = AsyncMock()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, msg: OutboundMessage) -> None:
        await self.sent(msg)

    async def send_progress_update(
        self,
        msg: OutboundMessage,
        *,
        operation_id: str,
        terminal: bool,
    ) -> None:
        await self.updated(msg, operation_id=operation_id, terminal=terminal)


def _manager(channel: _Channel) -> ChannelManager:
    config = Config.model_validate({"channels": {"websocket": {"enabled": False}}})
    manager = ChannelManager(config, channel.bus)
    manager.channels[channel.name] = channel
    return manager


@pytest.mark.asyncio
async def test_editable_channel_updates_one_operation_message_for_every_phase() -> None:
    channel = _Channel(editable=True)
    manager = _manager(channel)
    events = (
        _event(RagOperation.INGEST, RagPhase.QUEUED, RagProgressState.QUEUED),
        _event(RagOperation.INGEST, RagPhase.PARSING, RagProgressState.RUNNING),
        _event(RagOperation.INGEST, RagPhase.COMPLETED, RagProgressState.COMPLETED),
    )

    for event in events:
        await manager._send_once(
            channel,
            OutboundMessage(channel="mock", chat_id="chat", content=event.content, event=event),
        )

    assert channel.updated.await_count == 3
    assert {call.kwargs["operation_id"] for call in channel.updated.await_args_list} == {
        "a" * 32
    }
    assert channel.updated.await_args_list[-1].kwargs["terminal"] is True
    channel.sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_plain_channel_query_sends_start_and_failure_but_suppresses_success() -> None:
    channel = _Channel(editable=False)
    manager = _manager(channel)
    events = (
        _event(RagOperation.QUERY, RagPhase.QUERYING, RagProgressState.RUNNING),
        _event(RagOperation.QUERY, RagPhase.RERANKING, RagProgressState.RUNNING),
        _event(RagOperation.QUERY, RagPhase.COMPLETED, RagProgressState.COMPLETED),
        _event(RagOperation.QUERY, RagPhase.FAILED, RagProgressState.FAILED),
    )

    for event in events:
        await manager._send_once(
            channel,
            OutboundMessage(channel="mock", chat_id="chat", content=event.content, event=event),
        )

    assert [call.args[0].event.phase for call in channel.sent.await_args_list] == [
        RagPhase.QUERYING,
        RagPhase.FAILED,
    ]


@pytest.mark.asyncio
async def test_plain_channel_ingestion_sends_only_queue_and_terminal() -> None:
    channel = _Channel(editable=False)
    manager = _manager(channel)
    events = (
        _event(RagOperation.INGEST, RagPhase.QUEUED, RagProgressState.QUEUED),
        _event(RagOperation.INGEST, RagPhase.EMBEDDING, RagProgressState.RUNNING),
        _event(RagOperation.INGEST, RagPhase.COMPLETED, RagProgressState.COMPLETED),
    )

    for event in events:
        await manager._send_once(
            channel,
            OutboundMessage(channel="mock", chat_id="chat", content=event.content, event=event),
        )

    assert [call.args[0].event.phase for call in channel.sent.await_args_list] == [
        RagPhase.QUEUED,
        RagPhase.COMPLETED,
    ]
