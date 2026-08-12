import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.context import (
    RequestContext,
    bind_request_context,
    current_request_context,
    reset_request_context,
)
from nanobot.bus.events import (
    ConversationScope,
    InboundMessage,
    InboundMessageCapabilities,
)
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse, ToolCallRequest
from nanobot.rag.types import (
    ChunkKey,
    DocumentId,
    RagEvidence,
    RagSearchResult,
    SearchStatus,
    SourceKind,
    SourceLocation,
)
from nanobot.session.turn_continuation import INTERNAL_CONTINUATION_META


class _ContextRecordingTool:
    name = "cron"
    concurrency_safe = False

    def __init__(self) -> None:
        self.contexts: list[dict] = []
        self.runtimes: list[object] = []

    async def execute(self, **_kwargs) -> str:
        ctx = current_request_context()
        assert ctx is not None
        self.runtimes.append(ctx.runtime)
        self.contexts.append({
            "channel": ctx.channel,
            "chat_id": ctx.chat_id,
            "metadata": ctx.metadata,
            "session_key": ctx.session_key,
        })
        return "created"


class _Tools:
    def __init__(self, tool: _ContextRecordingTool) -> None:
        self.tool = tool

    @property
    def tool_names(self) -> list[str]:
        return ["cron"]

    def get(self, name: str):
        return self.tool if name == "cron" else None

    def get_definitions(self) -> list:
        return []

    def prepare_call(self, name: str, arguments: dict):
        return (self.tool, arguments, None) if name == "cron" else (None, arguments, None)


class _RagApplication:
    available = True
    unavailable_message = "RAG unavailable"

    def __init__(self) -> None:
        self.searches: list[tuple[object, str]] = []

    async def search(self, context, question: str) -> RagSearchResult:
        self.searches.append((context, question))
        return RagSearchResult(
            status=SearchStatus.EVIDENCE,
            evidence=(
                RagEvidence(
                    chunk_key=ChunkKey(1),
                    document_id=DocumentId("a" * 32),
                    filename="private.txt",
                    text="SELECTED_PRIVATE_EVIDENCE",
                    location=SourceLocation(
                        kind=SourceKind.TEXT_LINES,
                        line_start=2,
                        line_end=3,
                    ),
                    fusion_score=0.5,
                    reranker_score=0.9,
                ),
            ),
        )

    async def add(self, context, attachments):
        return "added"

    async def status(self, context, job_id):
        return "status"

    async def list_documents(self, context):
        return "list"

    async def delete(self, context, document_id):
        return "deleted"

    async def ask(self, context, question):
        return "answer"


@pytest.mark.asyncio
async def test_loop_binds_request_context_for_tool_execution(tmp_path: Path) -> None:
    provider = MagicMock()
    calls = {"n": 0}

    async def chat_with_retry(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="call_1", name="cron", arguments={"action": "add"})],
            )
        return LLMResponse(content="done", tool_calls=[])

    provider.chat_with_retry = chat_with_retry
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    cron = _ContextRecordingTool()
    loop.tools = _Tools(cron)

    metadata = {"slack": {"thread_ts": "111.222", "channel_type": "channel"}}
    runtime = loop.llm_runtime()
    await loop._run_agent_loop(
        [],
        runtime=runtime,
        channel="slack",
        chat_id="C123",
        metadata=metadata,
        session_key="slack:C123:111.222",
    )

    assert cron.contexts[-1] == {
        "channel": "slack",
        "chat_id": "C123",
        "metadata": metadata,
        "session_key": "slack:C123:111.222",
    }
    assert cron.runtimes[-1] is runtime


def test_turn_request_context_contains_immutable_trusted_channel_capabilities(
    tmp_path: Path,
) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    msg = InboundMessage(
        channel="websocket",
        sender_id="transport-user",
        chat_id="private-chat",
        content="hello",
        capabilities=InboundMessageCapabilities(
            conversation_scope=ConversationScope.PRIVATE,
            stable_authenticated_sender=True,
            authenticated_sender_id="authenticated-user",
            document_attachments=True,
        ),
    )
    turn = MagicMock()
    turn.session = MagicMock(metadata={})
    turn.delivery.route.channel = "websocket"
    turn.delivery.route.chat_id = "private-chat"
    turn.msg = msg
    turn.session_key = "websocket:private-chat"
    turn.original_user_text = "hello"
    turn.runtime = loop.llm_runtime()
    turn.attributes = {}
    turn.turn_id = "turn-1"

    request = loop._request_context_for_turn(turn)

    assert request.attributes["rag_capabilities"] == {
        "conversation_scope": "private",
        "stable_authenticated_sender": True,
        "authenticated_sender_id": "authenticated-user",
        "document_attachments": True,
    }


@pytest.mark.asyncio
async def test_agent_loop_registers_rag_tool_and_sends_only_selected_evidence(
    tmp_path: Path,
) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    captured_messages: list[list[dict]] = []
    calls = 0

    async def chat_with_retry(**kwargs):
        nonlocal calls
        calls += 1
        captured_messages.append(kwargs["messages"])
        if calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="rag-1",
                        name="search_knowledge_base",
                        arguments={"question": "deployment"},
                    )
                ],
            )
        return LLMResponse(content="answer with citation", tool_calls=[])

    provider.chat_with_retry = chat_with_retry
    application = _RagApplication()
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        rag_application=application,
    )
    request = RequestContext(
        channel="websocket",
        chat_id="private-chat",
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

    await loop._run_agent_loop(
        [{"role": "user", "content": "How is deployment configured?"}],
        runtime=loop.llm_runtime(),
        request_context=request,
    )

    assert "search_knowledge_base" in loop.tool_names
    assert application.searches[0][0].sender_id == "trusted-user"
    tool_result = next(
        message["content"]
        for message in captured_messages[-1]
        if message.get("role") == "tool"
    )
    assert "SELECTED_PRIVATE_EVIDENCE" in tool_result
    assert "untrusted_rag_evidence" in tool_result
    assert "引用" in tool_result


@pytest.mark.asyncio
async def test_agent_can_answer_without_rag_and_cannot_override_principal(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    calls = 0

    async def chat_with_retry(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="rag-override",
                        name="search_knowledge_base",
                        arguments={"question": "secret", "principal_id": "victim"},
                    )
                ],
            )
        return LLMResponse(content="answered without private evidence", tool_calls=[])

    provider.chat_with_retry = chat_with_retry
    application = _RagApplication()
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        rag_application=application,
    )
    request = RequestContext(
        channel="websocket",
        chat_id="private-chat",
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

    await loop._run_agent_loop(
        [{"role": "user", "content": "hello"}],
        runtime=loop.llm_runtime(),
        request_context=request,
    )

    assert application.searches == []


def test_request_context_nested_bind_restores_outer_context() -> None:
    outer = RequestContext(channel="slack", chat_id="outer", session_key="slack:outer")
    inner = RequestContext(channel="email", chat_id="inner", session_key="email:inner")

    outer_token = bind_request_context(outer)
    try:
        assert current_request_context() is outer
        inner_token = bind_request_context(inner)
        try:
            assert current_request_context() is inner
        finally:
            reset_request_context(inner_token)
        assert current_request_context() is outer
    finally:
        reset_request_context(outer_token)

    assert current_request_context() is None


@pytest.mark.asyncio
async def test_request_context_bindings_are_isolated_between_concurrent_tasks() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def observe(ctx: RequestContext, *, wait_first: bool) -> RequestContext | None:
        token = bind_request_context(ctx)
        try:
            if wait_first:
                entered.set()
                await release.wait()
            else:
                await entered.wait()
                release.set()
            await asyncio.sleep(0)
            return current_request_context()
        finally:
            reset_request_context(token)

    first = RequestContext(channel="feishu", chat_id="first", session_key="feishu:first")
    second = RequestContext(channel="telegram", chat_id="second", session_key="telegram:second")

    observed = await asyncio.gather(
        observe(first, wait_first=True),
        observe(second, wait_first=False),
    )

    assert observed == [first, second]
    assert current_request_context() is None


@pytest.mark.asyncio
async def test_agent_loop_restores_outer_request_context_after_runner_exception(
    tmp_path: Path,
) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    outer = RequestContext(channel="test", chat_id="outer", session_key="test:outer")
    runtime = loop.llm_runtime()

    async def fail_run(spec):
        current = current_request_context()
        assert current is not None
        assert spec.runtime is runtime
        assert current.runtime is runtime
        assert current.channel == "slack"
        assert current.chat_id == "C123"
        assert current.session_key == "slack:C123:111.222"
        assert current.original_user_text == "  unchanged user text  "
        raise RuntimeError("runner failed")

    loop.runner.run = AsyncMock(side_effect=fail_run)
    outer_token = bind_request_context(outer)
    try:
        with pytest.raises(RuntimeError, match="runner failed"):
            await loop._run_agent_loop(
                [],
                runtime=runtime,
                channel="slack",
                chat_id="C123",
                session_key="slack:C123:111.222",
                original_user_text="  unchanged user text  ",
            )
        assert current_request_context() is outer
    finally:
        reset_request_context(outer_token)

    assert current_request_context() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({}, "  original user text  "),
        ({INTERNAL_CONTINUATION_META: True}, None),
    ],
)
async def test_process_message_captures_original_text_before_restore(
    tmp_path: Path,
    metadata: dict,
    expected: str | None,
) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    runtime = loop.llm_runtime()
    seen: list[tuple[str | None, object]] = []

    async def stop_after_capture(ctx) -> str:
        seen.append((ctx.original_user_text, ctx.runtime))
        raise RuntimeError("captured before restore")

    loop._restore_turn = stop_after_capture  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="captured before restore"):
        await loop._process_message(
            InboundMessage(
                channel="slack",
                sender_id="user",
                chat_id="C123",
                content="  original user text  ",
                metadata=metadata,
            ),
            runtime=runtime,
        )

    assert seen == [(expected, runtime)]
