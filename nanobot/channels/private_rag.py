"""Dependency-free channel facts used to authorize private RAG."""

from __future__ import annotations

from nanobot.bus.events import ConversationScope, InboundMessageCapabilities


def _trusted_capabilities(
    scope: ConversationScope,
    sender_id: str,
    *,
    document_attachments: bool,
    message_editing: bool,
) -> InboundMessageCapabilities:
    return InboundMessageCapabilities(
        conversation_scope=scope,
        stable_authenticated_sender=True,
        authenticated_sender_id=sender_id,
        document_attachments=document_attachments,
        message_editing=message_editing,
    )


def telegram_rag_capabilities(chat_type: str, *, user_id: int) -> InboundMessageCapabilities:
    scope = {
        "private": ConversationScope.PRIVATE,
        "group": ConversationScope.GROUP,
        "supergroup": ConversationScope.GROUP,
        "channel": ConversationScope.PUBLIC,
    }.get(chat_type, ConversationScope.UNKNOWN)
    return _trusted_capabilities(
        scope,
        str(user_id),
        document_attachments=True,
        message_editing=True,
    )


def discord_rag_capabilities(*, is_dm: bool, user_id: str) -> InboundMessageCapabilities:
    return _trusted_capabilities(
        ConversationScope.PRIVATE if is_dm else ConversationScope.GROUP,
        user_id,
        document_attachments=True,
        message_editing=True,
    )


def slack_rag_capabilities(
    channel_type: str, *, user_id: str
) -> InboundMessageCapabilities:
    scope = {
        "im": ConversationScope.PRIVATE,
        "mpim": ConversationScope.GROUP,
        "group": ConversationScope.GROUP,
        "channel": ConversationScope.PUBLIC,
    }.get(channel_type, ConversationScope.UNKNOWN)
    return _trusted_capabilities(
        scope,
        user_id,
        document_attachments=True,
        message_editing=True,
    )


def websocket_rag_capabilities(*, trusted_webui: bool) -> InboundMessageCapabilities:
    if not trusted_webui:
        return InboundMessageCapabilities()
    return _trusted_capabilities(
        ConversationScope.PRIVATE,
        "webui-personal",
        document_attachments=True,
        message_editing=True,
    )


def feishu_rag_capabilities(
    chat_type: str, *, user_id: str
) -> InboundMessageCapabilities:
    scope = {
        "p2p": ConversationScope.PRIVATE,
        "group": ConversationScope.GROUP,
    }.get(chat_type, ConversationScope.UNKNOWN)
    return _trusted_capabilities(
        scope,
        user_id,
        document_attachments=True,
        message_editing=True,
    )


def dingtalk_rag_capabilities(
    conversation_type: str | None, *, user_id: str
) -> InboundMessageCapabilities:
    if conversation_type == "1":
        scope = ConversationScope.PRIVATE
    elif conversation_type == "2":
        scope = ConversationScope.GROUP
    else:
        scope = ConversationScope.UNKNOWN
    return _trusted_capabilities(
        scope,
        user_id,
        document_attachments=True,
        message_editing=False,
    )


def qq_rag_capabilities(*, is_group: bool, user_id: str) -> InboundMessageCapabilities:
    return _trusted_capabilities(
        ConversationScope.GROUP if is_group else ConversationScope.PRIVATE,
        user_id,
        document_attachments=True,
        message_editing=False,
    )


def napcat_rag_capabilities(
    *, message_type: str, user_id: str
) -> InboundMessageCapabilities:
    scope = {
        "private": ConversationScope.PRIVATE,
        "group": ConversationScope.GROUP,
    }.get(message_type, ConversationScope.UNKNOWN)
    return _trusted_capabilities(
        scope,
        user_id,
        document_attachments=False,
        message_editing=False,
    )


def weixin_rag_capabilities(*, from_user_id: str) -> InboundMessageCapabilities:
    return _trusted_capabilities(
        (
            ConversationScope.GROUP
            if from_user_id.endswith("@chatroom")
            else ConversationScope.PRIVATE
        ),
        from_user_id,
        document_attachments=True,
        message_editing=False,
    )


def wecom_rag_capabilities(
    chat_type: str, *, user_id: str
) -> InboundMessageCapabilities:
    scope = {
        "single": ConversationScope.PRIVATE,
        "group": ConversationScope.GROUP,
    }.get(chat_type, ConversationScope.UNKNOWN)
    return _trusted_capabilities(
        scope,
        user_id,
        document_attachments=True,
        message_editing=False,
    )


def mochat_rag_capabilities(
    *, target_kind: str, is_group: bool, user_id: str
) -> InboundMessageCapabilities:
    if is_group:
        scope = ConversationScope.GROUP
    elif target_kind == "session":
        scope = ConversationScope.PRIVATE
    else:
        scope = ConversationScope.UNKNOWN
    return _trusted_capabilities(
        scope,
        user_id,
        document_attachments=False,
        message_editing=False,
    )


def matrix_rag_capabilities(
    *, member_count: int | None, user_id: str
) -> InboundMessageCapabilities:
    if member_count == 2:
        scope = ConversationScope.PRIVATE
    elif isinstance(member_count, int) and member_count > 2:
        scope = ConversationScope.GROUP
    else:
        scope = ConversationScope.UNKNOWN
    return _trusted_capabilities(
        scope,
        user_id,
        document_attachments=True,
        message_editing=True,
    )


def mattermost_rag_capabilities(
    channel_type: str, *, user_id: str
) -> InboundMessageCapabilities:
    scope = {
        "dm": ConversationScope.PRIVATE,
        "group": ConversationScope.GROUP,
        "private": ConversationScope.GROUP,
        "public": ConversationScope.PUBLIC,
    }.get(channel_type, ConversationScope.UNKNOWN)
    return _trusted_capabilities(
        scope,
        user_id,
        document_attachments=True,
        message_editing=True,
    )


def signal_rag_capabilities(
    *, is_group: bool, user_id: str
) -> InboundMessageCapabilities:
    return _trusted_capabilities(
        ConversationScope.GROUP if is_group else ConversationScope.PRIVATE,
        user_id,
        document_attachments=True,
        message_editing=False,
    )


def whatsapp_rag_capabilities(
    *, is_group: bool, user_id: str
) -> InboundMessageCapabilities:
    return _trusted_capabilities(
        ConversationScope.GROUP if is_group else ConversationScope.PRIVATE,
        user_id,
        document_attachments=True,
        message_editing=False,
    )


def msteams_rag_capabilities(
    conversation_type: str, *, user_id: str
) -> InboundMessageCapabilities:
    scope = {
        "personal": ConversationScope.PRIVATE,
        "groupChat": ConversationScope.GROUP,
        "channel": ConversationScope.PUBLIC,
    }.get(conversation_type, ConversationScope.UNKNOWN)
    return _trusted_capabilities(
        scope,
        user_id,
        document_attachments=False,
        message_editing=True,
    )


def email_rag_capabilities() -> InboundMessageCapabilities:
    """Email headers cannot prove a private, per-person security principal."""
    return InboundMessageCapabilities(document_attachments=True)


__all__ = [
    "dingtalk_rag_capabilities",
    "discord_rag_capabilities",
    "email_rag_capabilities",
    "feishu_rag_capabilities",
    "matrix_rag_capabilities",
    "mattermost_rag_capabilities",
    "mochat_rag_capabilities",
    "msteams_rag_capabilities",
    "napcat_rag_capabilities",
    "qq_rag_capabilities",
    "signal_rag_capabilities",
    "slack_rag_capabilities",
    "telegram_rag_capabilities",
    "wecom_rag_capabilities",
    "weixin_rag_capabilities",
    "websocket_rag_capabilities",
    "whatsapp_rag_capabilities",
]
