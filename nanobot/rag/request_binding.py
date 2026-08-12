"""Derive private RAG context from server-bound channel capability snapshots."""

from __future__ import annotations

from typing import cast

from nanobot.agent.tools.context import RequestContext
from nanobot.bus.events import ConversationScope
from nanobot.rag.identity import RagAuthorizationError, derive_principal_id
from nanobot.rag.types import RagErrorCode, RagRequestContext


def rag_context_from_tool_request(request: RequestContext) -> RagRequestContext:
    raw = request.attributes.get("rag_capabilities")
    if not isinstance(raw, dict):
        raise _untrusted()
    capabilities = cast(dict[str, object], raw)
    stable = capabilities.get("stable_authenticated_sender")
    sender_id = capabilities.get("authenticated_sender_id")
    scope_value = capabilities.get("conversation_scope")
    if stable is not True or not isinstance(sender_id, str) or not sender_id.strip():
        raise _untrusted()
    try:
        scope = ConversationScope(str(scope_value))
    except ValueError as exc:
        raise _untrusted() from exc
    if scope is not ConversationScope.PRIVATE:
        raise RagAuthorizationError(
            RagErrorCode.NON_PRIVATE_CONVERSATION,
            "私人 RAG 仅支持渠道确认的私聊会话",
        )
    return RagRequestContext(
        principal_id=derive_principal_id(request.channel, sender_id),
        channel=request.channel,
        sender_id=sender_id,
        chat_id=request.chat_id,
        conversation_scope=scope,
        authenticated_sender=True,
    )


def _untrusted() -> RagAuthorizationError:
    return RagAuthorizationError(
        RagErrorCode.UNTRUSTED_IDENTITY,
        "当前渠道未提供稳定且经过认证的发送者身份",
    )


__all__ = ["rag_context_from_tool_request"]
