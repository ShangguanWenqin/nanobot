from __future__ import annotations

import pytest

from nanobot.bus.events import ConversationScope
from nanobot.channels.private_rag import (
    email_rag_capabilities,
    matrix_rag_capabilities,
    mattermost_rag_capabilities,
    msteams_rag_capabilities,
    signal_rag_capabilities,
    whatsapp_rag_capabilities,
)


@pytest.mark.parametrize(
    ("member_count", "expected"),
    [
        (2, ConversationScope.PRIVATE),
        (3, ConversationScope.GROUP),
        (None, ConversationScope.UNKNOWN),
        (1, ConversationScope.UNKNOWN),
    ],
)
def test_matrix_requires_an_exact_two_member_room(
    member_count: int | None, expected: ConversationScope
) -> None:
    capabilities = matrix_rag_capabilities(
        member_count=member_count,
        user_id="@alice:example.org",
    )

    assert capabilities.conversation_scope is expected
    assert capabilities.document_attachments is True


@pytest.mark.parametrize(
    ("channel_type", "expected"),
    [
        ("dm", ConversationScope.PRIVATE),
        ("group", ConversationScope.GROUP),
        ("private", ConversationScope.GROUP),
        ("public", ConversationScope.PUBLIC),
        ("unknown", ConversationScope.UNKNOWN),
    ],
)
def test_mattermost_uses_server_channel_type(
    channel_type: str, expected: ConversationScope
) -> None:
    capabilities = mattermost_rag_capabilities(channel_type, user_id="user-id")

    assert capabilities.conversation_scope is expected
    assert capabilities.document_attachments is True


def test_signal_and_whatsapp_distinguish_direct_from_group() -> None:
    signal_dm = signal_rag_capabilities(is_group=False, user_id="+8613800000000")
    signal_group = signal_rag_capabilities(is_group=True, user_id="+8613800000000")
    whatsapp_dm = whatsapp_rag_capabilities(is_group=False, user_id="8613800000000")
    whatsapp_group = whatsapp_rag_capabilities(is_group=True, user_id="8613800000000")

    assert signal_dm.conversation_scope is ConversationScope.PRIVATE
    assert signal_group.conversation_scope is ConversationScope.GROUP
    assert whatsapp_dm.conversation_scope is ConversationScope.PRIVATE
    assert whatsapp_group.conversation_scope is ConversationScope.GROUP
    assert signal_dm.document_attachments is True
    assert whatsapp_dm.document_attachments is True


@pytest.mark.parametrize(
    ("conversation_type", "expected"),
    [
        ("personal", ConversationScope.PRIVATE),
        ("groupChat", ConversationScope.GROUP),
        ("channel", ConversationScope.PUBLIC),
        ("", ConversationScope.UNKNOWN),
    ],
)
def test_msteams_requires_explicit_conversation_type(
    conversation_type: str, expected: ConversationScope
) -> None:
    capabilities = msteams_rag_capabilities(
        conversation_type,
        user_id="aad-object-id",
    )

    assert capabilities.conversation_scope is expected
    assert capabilities.document_attachments is False


def test_email_fails_closed_even_when_attachments_are_available() -> None:
    capabilities = email_rag_capabilities()

    assert capabilities.conversation_scope is ConversationScope.UNKNOWN
    assert capabilities.stable_authenticated_sender is False
    assert capabilities.authenticated_sender_id is None
    assert capabilities.document_attachments is True
