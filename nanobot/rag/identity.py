"""Server-owned private RAG identity derivation and authorization policy."""

from __future__ import annotations

import hashlib
import re

from nanobot.bus.events import ConversationScope, InboundMessage
from nanobot.rag.types import PrincipalId, RagErrorCode, RagRequestContext

_PRINCIPAL_DOMAIN = b"nanobot-private-rag-principal-v1\0"
_PRINCIPAL_DIRECTORY = re.compile(r"^[0-9a-f]{64}$")


class RagAuthorizationError(PermissionError):
    """Safe policy failure that carries a stable user-facing error category."""

    def __init__(self, code: RagErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canonical_identity_part(name: str, value: str) -> str:
    if not value:
        raise ValueError(f"{name} must not be empty")
    if value != value.strip():
        raise ValueError(f"{name} must not have surrounding whitespace")
    if "\0" in value:
        raise ValueError(f"{name} must not contain NUL")
    return value


def derive_principal_id(channel: str, sender_id: str) -> PrincipalId:
    """Derive a path-safe private principal from authenticated channel facts."""

    canonical_channel = _canonical_identity_part("channel", channel)
    canonical_sender = _canonical_identity_part("sender_id", sender_id)
    payload = (
        _PRINCIPAL_DOMAIN
        + canonical_channel.encode("utf-8")
        + b"\0"
        + canonical_sender.encode("utf-8")
    )
    return PrincipalId(hashlib.sha256(payload).hexdigest())


def principal_directory_name(principal_id: PrincipalId) -> str:
    """Validate the only identifier allowed as a per-principal directory name."""

    value = str(principal_id)
    if _PRINCIPAL_DIRECTORY.fullmatch(value) is None:
        raise ValueError("principal_id is not a valid system-derived directory identifier")
    return value


def authorize_private_rag(message: InboundMessage) -> RagRequestContext:
    """Build immutable RAG context exclusively from server/channel-owned fields."""

    capabilities = message.capabilities
    if not capabilities.stable_authenticated_sender:
        raise RagAuthorizationError(
            RagErrorCode.UNTRUSTED_IDENTITY,
            "The channel did not provide a stable authenticated sender identity",
        )
    if capabilities.conversation_scope is not ConversationScope.PRIVATE:
        raise RagAuthorizationError(
            RagErrorCode.NON_PRIVATE_CONVERSATION,
            "Private RAG is available only in a channel-confirmed private conversation",
        )

    authenticated_sender_id = capabilities.authenticated_sender_id or message.sender_id
    principal_id = derive_principal_id(message.channel, authenticated_sender_id)
    return RagRequestContext(
        principal_id=principal_id,
        channel=message.channel,
        sender_id=authenticated_sender_id,
        chat_id=message.chat_id,
        conversation_scope=capabilities.conversation_scope,
        authenticated_sender=capabilities.stable_authenticated_sender,
    )


__all__ = [
    "RagAuthorizationError",
    "authorize_private_rag",
    "derive_principal_id",
    "principal_directory_name",
]
