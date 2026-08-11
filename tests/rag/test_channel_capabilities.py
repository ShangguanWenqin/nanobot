from __future__ import annotations

import pytest

from nanobot.bus.events import (
    ConversationScope,
    InboundMessageCapabilities,
    OutboundMessage,
)
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel


class CapabilityChannel(BaseChannel):
    name = "capability-test"

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, msg: OutboundMessage) -> None:
        return None


def test_message_capabilities_are_immutable_and_fail_closed_by_default() -> None:
    capabilities = InboundMessageCapabilities()

    assert capabilities.conversation_scope is ConversationScope.UNKNOWN
    assert capabilities.stable_authenticated_sender is False
    assert capabilities.document_attachments is False
    assert capabilities.message_editing is False


@pytest.mark.parametrize(
    "scope",
    [
        ConversationScope.PRIVATE,
        ConversationScope.GROUP,
        ConversationScope.PUBLIC,
        ConversationScope.UNKNOWN,
    ],
)
def test_all_conversation_scopes_are_explicit(scope: ConversationScope) -> None:
    assert InboundMessageCapabilities(conversation_scope=scope).conversation_scope is scope


@pytest.mark.asyncio
async def test_base_channel_preserves_explicit_trusted_capabilities() -> None:
    bus = MessageBus()
    channel = CapabilityChannel({"allowFrom": ["sender-1"]}, bus)
    capabilities = InboundMessageCapabilities(
        conversation_scope=ConversationScope.PRIVATE,
        stable_authenticated_sender=True,
        document_attachments=True,
        message_editing=True,
    )

    await channel._handle_message(
        sender_id="sender-1",
        chat_id="chat-1",
        content="hello",
        is_dm=True,
        capabilities=capabilities,
    )

    inbound = await bus.consume_inbound()
    assert inbound.capabilities == capabilities


@pytest.mark.asyncio
async def test_legacy_is_dm_does_not_implicitly_grant_private_rag_capability() -> None:
    bus = MessageBus()
    channel = CapabilityChannel({"allowFrom": ["sender-1"]}, bus)

    await channel._handle_message(
        sender_id="sender-1",
        chat_id="chat-1",
        content="hello",
        is_dm=True,
    )

    inbound = await bus.consume_inbound()
    assert inbound.capabilities == InboundMessageCapabilities()
