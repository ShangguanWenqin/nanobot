from __future__ import annotations

import hashlib
import re
from dataclasses import FrozenInstanceError

import pytest

from nanobot.bus.events import (
    ConversationScope,
    InboundMessage,
    InboundMessageCapabilities,
)
from nanobot.rag.identity import (
    RagAuthorizationError,
    authorize_private_rag,
    derive_principal_id,
    principal_directory_name,
)
from nanobot.rag.types import RagErrorCode


def _message(
    *,
    channel: str = "telegram",
    sender_id: str = "42",
    chat_id: str = "chat-a",
    scope: ConversationScope = ConversationScope.PRIVATE,
    authenticated: bool = True,
) -> InboundMessage:
    return InboundMessage(
        channel=channel,
        sender_id=sender_id,
        chat_id=chat_id,
        content="/rag status",
        capabilities=InboundMessageCapabilities(
            conversation_scope=scope,
            stable_authenticated_sender=authenticated,
            document_attachments=True,
        ),
    )


def test_principal_uses_domain_separated_channel_and_sender_hash() -> None:
    principal = derive_principal_id("telegram", "42")
    expected = hashlib.sha256(
        b"nanobot-private-rag-principal-v1\0telegram\0" + b"42"
    ).hexdigest()

    assert principal == expected
    assert re.fullmatch(r"[0-9a-f]{64}", principal)
    assert principal_directory_name(principal) == expected


def test_same_sender_on_different_channels_is_isolated() -> None:
    assert derive_principal_id("telegram", "42") != derive_principal_id("discord", "42")


def test_chat_and_session_routing_do_not_change_principal() -> None:
    first = _message(chat_id="chat-a")
    first.session_key_override = "attacker-controlled-session"
    second = _message(chat_id="chat-b")

    assert authorize_private_rag(first).principal_id == authorize_private_rag(second).principal_id


@pytest.mark.parametrize(
    "scope",
    [ConversationScope.GROUP, ConversationScope.PUBLIC, ConversationScope.UNKNOWN],
)
def test_non_private_or_unknown_scope_fails_closed(scope: ConversationScope) -> None:
    with pytest.raises(RagAuthorizationError) as caught:
        authorize_private_rag(_message(scope=scope))

    assert caught.value.code is RagErrorCode.NON_PRIVATE_CONVERSATION


def test_untrusted_or_empty_sender_identity_fails_closed() -> None:
    with pytest.raises(RagAuthorizationError) as caught:
        authorize_private_rag(_message(authenticated=False))
    assert caught.value.code is RagErrorCode.UNTRUSTED_IDENTITY

    with pytest.raises(ValueError, match="sender_id"):
        derive_principal_id("telegram", "")


def test_metadata_cannot_override_server_derived_principal() -> None:
    message = _message()
    message.metadata.update(
        {
            "principal_id": "attacker",
            "sender_id": "attacker",
            "channel": "discord",
        }
    )

    context = authorize_private_rag(message)

    assert context.principal_id == derive_principal_id("telegram", "42")
    assert context.channel == "telegram"
    assert context.sender_id == "42"
    with pytest.raises(FrozenInstanceError):
        context.principal_id = derive_principal_id("telegram", "attacker")  # type: ignore[misc]


@pytest.mark.parametrize(
    ("channel", "sender_id"),
    [("", "42"), (" telegram", "42"), ("telegram", " 42"), ("tele\0gram", "42")],
)
def test_non_canonical_identity_values_are_rejected(channel: str, sender_id: str) -> None:
    with pytest.raises(ValueError):
        derive_principal_id(channel, sender_id)
