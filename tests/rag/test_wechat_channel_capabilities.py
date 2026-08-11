from __future__ import annotations

import pytest

from nanobot.bus.events import ConversationScope
from nanobot.channels.private_rag import (
    mochat_rag_capabilities,
    wecom_rag_capabilities,
    weixin_rag_capabilities,
)


def test_weixin_private_user_and_chatroom_are_distinct() -> None:
    private = weixin_rag_capabilities(from_user_id="wxid_alice")
    group = weixin_rag_capabilities(from_user_id="123@chatroom")

    assert private.conversation_scope is ConversationScope.PRIVATE
    assert private.authenticated_sender_id == "wxid_alice"
    assert private.document_attachments is True
    assert group.conversation_scope is ConversationScope.GROUP


@pytest.mark.parametrize(
    ("chat_type", "expected"),
    [
        ("single", ConversationScope.PRIVATE),
        ("group", ConversationScope.GROUP),
        ("", ConversationScope.UNKNOWN),
    ],
)
def test_wecom_requires_explicit_chat_type(
    chat_type: str, expected: ConversationScope
) -> None:
    capabilities = wecom_rag_capabilities(chat_type, user_id="zhangsan")

    assert capabilities.conversation_scope is expected
    assert capabilities.document_attachments is True


def test_mochat_only_treats_non_group_session_as_private() -> None:
    private = mochat_rag_capabilities(
        target_kind="session", is_group=False, user_id="user-1"
    )
    group = mochat_rag_capabilities(
        target_kind="panel", is_group=True, user_id="user-1"
    )
    ambiguous_panel = mochat_rag_capabilities(
        target_kind="panel", is_group=False, user_id="user-1"
    )

    assert private.conversation_scope is ConversationScope.PRIVATE
    assert group.conversation_scope is ConversationScope.GROUP
    assert ambiguous_panel.conversation_scope is ConversationScope.UNKNOWN
    assert private.document_attachments is False
