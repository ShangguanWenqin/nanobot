from __future__ import annotations

import pytest

from nanobot.bus.events import ConversationScope
from nanobot.channels.private_rag import (
    dingtalk_rag_capabilities,
    feishu_rag_capabilities,
    napcat_rag_capabilities,
    qq_rag_capabilities,
)


@pytest.mark.parametrize(
    ("chat_type", "expected"),
    [
        ("p2p", ConversationScope.PRIVATE),
        ("group", ConversationScope.GROUP),
        ("topic", ConversationScope.UNKNOWN),
    ],
)
def test_feishu_scope_and_file_capability(
    chat_type: str, expected: ConversationScope
) -> None:
    capabilities = feishu_rag_capabilities(chat_type, user_id="ou_123")

    assert capabilities.conversation_scope is expected
    assert capabilities.authenticated_sender_id == "ou_123"
    assert capabilities.document_attachments is True


@pytest.mark.parametrize(
    ("conversation_type", "expected"),
    [
        ("1", ConversationScope.PRIVATE),
        ("2", ConversationScope.GROUP),
        (None, ConversationScope.UNKNOWN),
    ],
)
def test_dingtalk_only_treats_explicit_type_one_as_private(
    conversation_type: str | None, expected: ConversationScope
) -> None:
    capabilities = dingtalk_rag_capabilities(conversation_type, user_id="staff-1")

    assert capabilities.conversation_scope is expected
    assert capabilities.document_attachments is True


def test_qq_c2c_and_group_scopes_support_document_attachments() -> None:
    private = qq_rag_capabilities(is_group=False, user_id="openid-1")
    group = qq_rag_capabilities(is_group=True, user_id="openid-1")

    assert private.conversation_scope is ConversationScope.PRIVATE
    assert group.conversation_scope is ConversationScope.GROUP
    assert private.document_attachments is True


def test_napcat_reports_image_only_transport_without_claiming_documents() -> None:
    private = napcat_rag_capabilities(message_type="private", user_id="10001")
    unknown = napcat_rag_capabilities(message_type="notice", user_id="10001")

    assert private.conversation_scope is ConversationScope.PRIVATE
    assert private.document_attachments is False
    assert unknown.conversation_scope is ConversationScope.UNKNOWN
