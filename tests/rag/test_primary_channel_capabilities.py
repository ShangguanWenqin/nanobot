from __future__ import annotations

import pytest

from nanobot.bus.events import ConversationScope, InboundMessageCapabilities
from nanobot.channels.private_rag import (
    discord_rag_capabilities,
    slack_rag_capabilities,
    telegram_rag_capabilities,
    websocket_rag_capabilities,
)


@pytest.mark.parametrize(
    ("chat_type", "expected"),
    [
        ("private", ConversationScope.PRIVATE),
        ("group", ConversationScope.GROUP),
        ("supergroup", ConversationScope.GROUP),
        ("channel", ConversationScope.PUBLIC),
        ("future-type", ConversationScope.UNKNOWN),
    ],
)
def test_telegram_reports_authenticated_scope_and_numeric_sender(
    chat_type: str, expected: ConversationScope
) -> None:
    capabilities = telegram_rag_capabilities(chat_type, user_id=123)

    assert capabilities.conversation_scope is expected
    assert capabilities.stable_authenticated_sender is True
    assert capabilities.authenticated_sender_id == "123"
    assert capabilities.document_attachments is True
    assert capabilities.message_editing is True


def test_discord_distinguishes_dm_from_guild_and_uses_platform_user_id() -> None:
    private = discord_rag_capabilities(is_dm=True, user_id="456")
    guild = discord_rag_capabilities(is_dm=False, user_id="456")

    assert private.conversation_scope is ConversationScope.PRIVATE
    assert guild.conversation_scope is ConversationScope.GROUP
    assert private.authenticated_sender_id == "456"
    assert private.document_attachments is True


@pytest.mark.parametrize(
    ("channel_type", "expected"),
    [
        ("im", ConversationScope.PRIVATE),
        ("mpim", ConversationScope.GROUP),
        ("group", ConversationScope.GROUP),
        ("channel", ConversationScope.PUBLIC),
        ("", ConversationScope.UNKNOWN),
    ],
)
def test_slack_maps_socket_event_channel_type(
    channel_type: str, expected: ConversationScope
) -> None:
    capabilities = slack_rag_capabilities(channel_type, user_id="U123")

    assert capabilities.conversation_scope is expected
    assert capabilities.authenticated_sender_id == "U123"
    assert capabilities.document_attachments is True


def test_only_trusted_webui_gets_fixed_server_owned_private_identity() -> None:
    webui = websocket_rag_capabilities(trusted_webui=True)
    generic = websocket_rag_capabilities(trusted_webui=False)

    assert webui == InboundMessageCapabilities(
        conversation_scope=ConversationScope.PRIVATE,
        stable_authenticated_sender=True,
        authenticated_sender_id="webui-personal",
        document_attachments=True,
        message_editing=True,
    )
    assert generic == InboundMessageCapabilities()
