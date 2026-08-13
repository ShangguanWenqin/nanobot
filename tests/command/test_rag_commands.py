from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import (
    ConversationScope,
    InboundMessage,
    InboundMessageCapabilities,
)
from nanobot.bus.queue import MessageBus
from nanobot.command.builtin import build_help_text, builtin_command_palette
from nanobot.command.router import CommandContext, CommandRouter
from nanobot.rag.commands import register_rag_commands
from nanobot.rag.types import (
    ChunkKey,
    DocumentId,
    JobId,
    RagEvidence,
    RagSearchResult,
    SearchStatus,
    SourceKind,
    SourceLocation,
)


class FakeRagApplication:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.unavailable_message = "请安装 nanobot-ai[rag] 并启用 rag.enabled"
        self.calls: list[tuple[object, ...]] = []

    async def add(self, context, attachments):
        self.calls.append(("add", context, attachments))
        return "已接受入库任务 job-1"

    async def status(self, context, job_id):
        self.calls.append(("status", context, job_id))
        return "配额 10/100，1 个就绪文档"

    async def list_documents(self, context):
        self.calls.append(("list", context))
        return "doc-1 guide.txt ready"

    async def delete(self, context, document_id):
        self.calls.append(("delete", context, document_id))
        return "已开始删除"

    async def search(self, context, question):
        self.calls.append(("search", context, question))
        return RagSearchResult(
            status=SearchStatus.EVIDENCE,
            evidence=(
                RagEvidence(
                    chunk_key=ChunkKey(1),
                    document_id=DocumentId("a" * 32),
                    filename="guide.txt",
                    text="安装步骤",
                    location=SourceLocation(
                        kind=SourceKind.PDF_PAGE,
                        page=3,
                    ),
                    fusion_score=0.5,
                    reranker_score=0.9,
                ),
            ),
        )


def _message(content: str, *, media: list[str] | None = None) -> InboundMessage:
    return InboundMessage(
        channel="websocket",
        sender_id="transport-user",
        chat_id="private-chat",
        content=content,
        media=media or [],
        capabilities=InboundMessageCapabilities(
            conversation_scope=ConversationScope.PRIVATE,
            stable_authenticated_sender=True,
            authenticated_sender_id="trusted-user",
            document_attachments=True,
        ),
    )


async def _dispatch(router: CommandRouter, message: InboundMessage):
    return await router.dispatch(
        CommandContext(
            msg=message,
            session=None,
            key="websocket:private-chat",
            raw=message.content,
            loop=SimpleNamespace(),
        )
    )


def test_agent_loop_routes_rag_add_before_document_attachment_adaptation(
    tmp_path: Path,
) -> None:
    document = tmp_path / "guide.txt"
    document.write_text("private guide", encoding="utf-8")
    application = FakeRagApplication()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        rag_application=application,
    )

    response = asyncio.run(
        loop._process_message(_message("/rag add", media=[str(document)]))
    )

    assert response is not None
    assert "job-1" in response.content
    assert application.calls[0][0] == "add"
    assert application.calls[0][2][0].source_path == document


@pytest.mark.asyncio
async def test_rag_add_requires_explicit_command_and_passes_only_current_attachments(
    tmp_path: Path,
) -> None:
    first = tmp_path / "guide.txt"
    second = tmp_path / "config.yaml"
    first.write_text("guide")
    second.write_text("config: true")
    application = FakeRagApplication()
    router = CommandRouter()
    register_rag_commands(router, application)

    response = await _dispatch(router, _message("/rag add", media=[str(first), str(second)]))

    assert response is not None
    assert "job-1" in response.content
    call = application.calls[0]
    assert call[0] == "add"
    assert call[1].sender_id == "trusted-user"
    assert [item.source_path for item in call[2]] == [first, second]
    assert router.is_dispatchable_command("ordinary attachment") is False


@pytest.mark.asyncio
async def test_rag_add_without_attachments_and_malformed_delete_return_usage() -> None:
    application = FakeRagApplication()
    router = CommandRouter()
    register_rag_commands(router, application)

    add = await _dispatch(router, _message("/rag add"))
    delete = await _dispatch(router, _message("/rag delete"))

    assert add is not None and "附件" in add.content
    assert delete is not None and "用法" in delete.content
    assert application.calls == []


@pytest.mark.asyncio
async def test_status_list_delete_and_forced_ask_route_to_current_principal() -> None:
    application = FakeRagApplication()
    router = CommandRouter()
    register_rag_commands(router, application)

    responses = [
        await _dispatch(router, _message("/rag status")),
        await _dispatch(router, _message("/rag status " + "1" * 32)),
        await _dispatch(router, _message("/rag list")),
        await _dispatch(router, _message("/rag delete " + "2" * 32)),
        await _dispatch(router, _message("/rag ask 如何安装？")),
    ]

    assert all(response is not None for response in responses[:4])
    assert application.calls[0][2] is None
    assert application.calls[1][2] == JobId("1" * 32)
    assert application.calls[3][2] == DocumentId("2" * 32)
    assert responses[-1] is None
    assert application.calls[4][0] == "search"
    assert application.calls[4][2] == "如何安装？"


@pytest.mark.asyncio
async def test_rag_ask_injects_selected_evidence_and_citation_rule_into_agent_turn() -> None:
    application = FakeRagApplication()
    router = CommandRouter()
    register_rag_commands(router, application)
    message = _message("/rag ask 如何安装？")

    response = await _dispatch(router, message)

    assert response is None
    assert "如何安装？" in message.content
    assert "安装步骤" in message.content
    assert "guide.txt" in message.content
    assert "第 3 页" in message.content
    assert "引用" in message.content
    assert "untrusted_rag_evidence" in message.content


@pytest.mark.asyncio
async def test_rag_ask_returns_explicit_message_when_no_evidence() -> None:
    application = FakeRagApplication()

    async def no_evidence(context, question):
        application.calls.append(("search", context, question))
        return RagSearchResult(status=SearchStatus.NO_EVIDENCE)

    application.search = no_evidence
    router = CommandRouter()
    register_rag_commands(router, application)

    response = await _dispatch(router, _message("/rag ask unknown"))

    assert response is not None
    assert "没有提供充分依据" in response.content


@pytest.mark.asyncio
async def test_rag_commands_fail_closed_in_group_and_show_actionable_unavailable_message() -> None:
    unavailable = FakeRagApplication(available=False)
    router = CommandRouter()
    register_rag_commands(router, unavailable)

    unavailable_response = await _dispatch(router, _message("/rag status"))
    group = _message("/rag list")
    group.capabilities = InboundMessageCapabilities(
        conversation_scope=ConversationScope.GROUP,
        stable_authenticated_sender=True,
        authenticated_sender_id="trusted-user",
        document_attachments=True,
    )
    group_response = await _dispatch(router, group)

    assert unavailable_response is not None
    assert "nanobot-ai[rag]" in unavailable_response.content
    assert group_response is not None
    assert "私聊" in group_response.content
    assert unavailable.calls == []


@pytest.mark.asyncio
async def test_rag_ask_requires_nonempty_question() -> None:
    application = FakeRagApplication()
    router = CommandRouter()
    register_rag_commands(router, application)

    response = await _dispatch(router, _message("/rag ask"))

    assert response is not None
    assert "用法" in response.content
    assert application.calls == []


def test_rag_commands_are_documented_in_help_and_palette() -> None:
    commands = {item["command"] for item in builtin_command_palette()}
    help_text = build_help_text()

    assert {"/rag add", "/rag status", "/rag list", "/rag delete", "/rag ask"} <= commands
    assert "/rag status [job_id]" in help_text
    assert "/rag ask <question>" in help_text
