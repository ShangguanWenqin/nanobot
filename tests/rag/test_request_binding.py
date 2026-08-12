from __future__ import annotations

import pytest

from nanobot.agent.tools.context import RequestContext
from nanobot.rag.identity import RagAuthorizationError, derive_principal_id
from nanobot.rag.request_binding import rag_context_from_tool_request
from nanobot.rag.types import RagErrorCode


def test_tool_request_derives_principal_only_from_authenticated_capability_snapshot() -> None:
    request = RequestContext(
        channel="websocket",
        chat_id="private-chat",
        sender_id="untrusted-transport-value",
        attributes={
            "rag_capabilities": {
                "conversation_scope": "private",
                "stable_authenticated_sender": True,
                "authenticated_sender_id": "authenticated-user",
                "document_attachments": True,
            }
        },
    )

    context = rag_context_from_tool_request(request)

    assert context.sender_id == "authenticated-user"
    assert context.principal_id == derive_principal_id("websocket", "authenticated-user")


@pytest.mark.parametrize(
    "attributes",
    [
        {},
        {"rag_capabilities": {"conversation_scope": "private"}},
        {
            "rag_capabilities": {
                "conversation_scope": "group",
                "stable_authenticated_sender": True,
                "authenticated_sender_id": "user",
                "document_attachments": True,
            }
        },
    ],
)
def test_missing_untrusted_or_nonprivate_tool_context_fails_closed(
    attributes: dict[str, object],
) -> None:
    request = RequestContext(
        channel="websocket",
        chat_id="chat",
        sender_id="user",
        attributes=attributes,
    )

    with pytest.raises(RagAuthorizationError) as captured:
        rag_context_from_tool_request(request)

    assert captured.value.code in {
        RagErrorCode.UNTRUSTED_IDENTITY,
        RagErrorCode.NON_PRIVATE_CONVERSATION,
    }
