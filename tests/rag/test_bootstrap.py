from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nanobot.bus.events import ConversationScope
from nanobot.bus.queue import MessageBus
from nanobot.rag.bootstrap import LocalRagRuntime, build_rag_application
from nanobot.rag.config import RagConfig
from nanobot.rag.ingestion import IngestionAttachment
from nanobot.rag.library_status import RuntimeProfileReport
from nanobot.rag.local_inference import FakeEmbedder, FakeReranker
from nanobot.rag.types import PrincipalId, RagRequestContext, SearchStatus


class _TokenCodec:
    version = "test-tokenizer-v1"

    def encode(self, text: str) -> tuple[str, ...]:
        return tuple(text)

    def decode(self, token_ids: tuple[str, ...]) -> str:
        return "".join(token_ids)


def test_build_rag_application_resolves_isolated_principal_services(tmp_path: Path) -> None:
    config = RagConfig(enabled=True)
    config.storage.root = str(tmp_path / "rag")
    embedder = FakeEmbedder(dimension=8)
    runtime = LocalRagRuntime(
        query_embedder=embedder,
        batch_embedder=embedder,
        reranker=FakeReranker(),
        token_codec=_TokenCodec(),
        profiles=RuntimeProfileReport(
            query_embedding="cpu-float32",
            batch_embedding="cpu-float32",
            reranker="cpu-float32",
            embedding_profile_id=str(embedder.profile_id),
            reranker_profile_id="fake-reranker-v1",
        ),
    )

    application, manager = build_rag_application(config, MessageBus(), runtime)
    first = application._resolve(PrincipalId("a" * 64))  # pyright: ignore[reportPrivateUsage]
    same = application._resolve(PrincipalId("a" * 64))  # pyright: ignore[reportPrivateUsage]
    other = application._resolve(PrincipalId("b" * 64))  # pyright: ignore[reportPrivateUsage]

    assert first is same
    assert first.ingestion.store.paths.database != other.ingestion.store.paths.database
    assert manager is not None


@pytest.mark.asyncio
async def test_service_backed_application_ingests_and_searches_end_to_end(
    tmp_path: Path,
) -> None:
    config = RagConfig(enabled=True)
    config.storage.root = str(tmp_path / "rag")
    config.retrieval.acceptance_threshold_override = 0.0
    embedder = FakeEmbedder(dimension=8)
    runtime = LocalRagRuntime(
        query_embedder=embedder,
        batch_embedder=embedder,
        reranker=FakeReranker(acceptance_threshold=0.0),
        token_codec=_TokenCodec(),
        profiles=RuntimeProfileReport(
            query_embedding="cpu-float32",
            batch_embedding="cpu-float32",
            reranker="cpu-float32",
            embedding_profile_id=str(embedder.profile_id),
            reranker_profile_id="fake-reranker-v1",
        ),
    )
    application, manager = build_rag_application(config, MessageBus(), runtime)
    context = RagRequestContext(
        principal_id=PrincipalId("c" * 64),
        channel="websocket",
        sender_id="user-c",
        chat_id="private-c",
        conversation_scope=ConversationScope.PRIVATE,
        authenticated_sender=True,
    )
    source = tmp_path / "knowledge.txt"
    source.write_text("nanobot 使用 /rag add 将文件加入私人知识库。", encoding="utf-8")

    await manager.start()
    try:
        response = await application.add(
            context,
            (IngestionAttachment(source, source.name, "text/plain"),),
        )
        services = application._resolve(context.principal_id)  # pyright: ignore[reportPrivateUsage]
        for _ in range(100):
            if not services.status.status().active_jobs:
                break
            await asyncio.sleep(0.01)

        result = await application.search(context, "如何加入私人知识库？")
    finally:
        await manager.stop()

    assert "排队处理中" in response
    assert result.status is SearchStatus.EVIDENCE
    assert result.evidence[0].filename == "knowledge.txt"
