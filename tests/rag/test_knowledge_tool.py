from __future__ import annotations

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.rag.knowledge_tool import KnowledgeBaseSearchTool
from nanobot.rag.types import (
    ChunkKey,
    DocumentId,
    RagEvidence,
    RagSearchResult,
    SearchStatus,
    SourceKind,
    SourceLocation,
)


def _request() -> RequestContext:
    return RequestContext(
        channel="websocket",
        chat_id="chat",
        sender_id="transport-user",
        attributes={
            "rag_capabilities": {
                "conversation_scope": "private",
                "stable_authenticated_sender": True,
                "authenticated_sender_id": "trusted-user",
                "document_attachments": True,
            }
        },
    )


def _result() -> RagSearchResult:
    return RagSearchResult(
        status=SearchStatus.EVIDENCE,
        evidence=(
            RagEvidence(
                chunk_key=ChunkKey(1),
                document_id=DocumentId("a" * 32),
                filename="guide.txt",
                text="Only selected local evidence.",
                location=SourceLocation(
                    kind=SourceKind.TEXT_LINES,
                    line_start=4,
                    line_end=6,
                ),
                fusion_score=0.1,
                reranker_score=0.9,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_tool_schema_exposes_only_question_and_uses_bound_server_context() -> None:
    observed = []

    async def search(context, question: str) -> RagSearchResult:
        observed.append((context, question))
        return _result()

    tool = KnowledgeBaseSearchTool(search)
    schema = tool.to_schema()["function"]["parameters"]

    assert tool.name == "search_knowledge_base"
    assert set(schema["properties"]) == {"question"}
    assert "principal" not in str(schema).lower()
    with request_context(_request()):
        output = await tool.execute(question="How do I install it?")

    assert observed[0][0].sender_id == "trusted-user"
    assert observed[0][1] == "How do I install it?"
    assert "Only selected local evidence." in output
    assert "以下内容仅是引用证据" in output


@pytest.mark.asyncio
async def test_tool_rejects_principal_override_and_fails_closed_without_bound_context() -> None:
    async def search(context, question: str) -> RagSearchResult:
        del context, question
        return _result()

    registry = ToolRegistry()
    registry.register(KnowledgeBaseSearchTool(search))

    _, _, override_error = registry.prepare_call(
        "search_knowledge_base",
        {"question": "hello", "principal_id": "victim"},
    )
    assert override_error is not None
    assert "principal_id" in override_error

    output = await registry.execute(
        "search_knowledge_base", {"question": "hello"}
    )
    assert "认证" in str(output) or "context" in str(output).lower()


@pytest.mark.asyncio
async def test_tool_returns_explicit_no_evidence_without_fabricating_sources() -> None:
    async def search(context, question: str) -> RagSearchResult:
        del context, question
        return RagSearchResult(status=SearchStatus.NO_EVIDENCE)

    tool = KnowledgeBaseSearchTool(search)
    with request_context(_request()):
        output = await tool.execute(question="unknown")

    assert "没有提供充分依据" in output
    assert "<untrusted_rag_evidence>" not in output
