"""Slash-command routing for the optional private RAG application."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Protocol

from nanobot.bus.events import OutboundMessage
from nanobot.command.router import CommandContext, CommandRouter
from nanobot.rag.evidence import serialize_untrusted_evidence
from nanobot.rag.identity import RagAuthorizationError, authorize_private_rag
from nanobot.rag.ingestion import IngestionAttachment
from nanobot.rag.types import DocumentId, JobId, RagRequestContext, RagSearchResult, SearchStatus


class RagCommandApplication(Protocol):
    available: bool
    unavailable_message: str

    async def add(
        self,
        context: RagRequestContext,
        attachments: tuple[IngestionAttachment, ...],
    ) -> str: ...

    async def status(self, context: RagRequestContext, job_id: JobId | None) -> str: ...

    async def list_documents(self, context: RagRequestContext) -> str: ...

    async def delete(self, context: RagRequestContext, document_id: DocumentId) -> str: ...

    async def search(
        self,
        context: RagRequestContext,
        question: str,
    ) -> RagSearchResult: ...


class RagApplication(RagCommandApplication, Protocol):
    pass


class UnavailableRagApplication:
    available = False
    unavailable_message = (
        "私人 RAG 当前不可用。请安装 `nanobot-ai[rag]`、设置 `rag.enabled: true`，"
        "并确认本地模型已准备完成。"
    )

    async def add(
        self,
        context: RagRequestContext,
        attachments: tuple[IngestionAttachment, ...],
    ) -> str:  # pragma: no cover - guarded by available
        raise RuntimeError(self.unavailable_message)

    async def status(
        self,
        context: RagRequestContext,
        job_id: JobId | None,
    ) -> str:  # pragma: no cover - guarded by available
        raise RuntimeError(self.unavailable_message)

    async def list_documents(
        self,
        context: RagRequestContext,
    ) -> str:  # pragma: no cover - guarded by available
        raise RuntimeError(self.unavailable_message)

    async def delete(
        self,
        context: RagRequestContext,
        document_id: DocumentId,
    ) -> str:  # pragma: no cover - guarded by available
        raise RuntimeError(self.unavailable_message)

    async def search(
        self,
        context: RagRequestContext,
        question: str,
    ) -> RagSearchResult:  # pragma: no cover - guarded by available
        raise RuntimeError(self.unavailable_message)


def register_rag_commands(
    router: CommandRouter,
    application: RagCommandApplication,
) -> None:
    async def dispatch(ctx: CommandContext) -> OutboundMessage | None:
        try:
            context = authorize_private_rag(ctx.msg)
        except RagAuthorizationError as exc:
            message = (
                "私人 RAG 仅支持渠道确认的私聊，请转到私聊后重试。"
                if exc.code.value == "non_private_conversation"
                else "当前渠道未提供稳定且经过认证的用户身份，无法使用私人 RAG。"
            )
            return _response(ctx, message)
        if not application.available:
            return _response(ctx, application.unavailable_message)

        command = ctx.raw.strip()
        lowered = command.lower()
        if lowered == "/rag add":
            if not ctx.msg.media:
                return _response(ctx, "请在 `/rag add` 消息中附加至少一个文档附件。")
            attachments = tuple(_attachment(path) for path in ctx.msg.media)
            return _response(ctx, await application.add(context, attachments))
        if lowered == "/rag status":
            return _response(ctx, await application.status(context, None))
        if lowered.startswith("/rag status "):
            value = command[len("/rag status ") :].strip()
            try:
                job_id = JobId(_system_id(value))
            except ValueError:
                return _response(ctx, "用法：`/rag status [job_id]`")
            return _response(ctx, await application.status(context, job_id))
        if lowered == "/rag list":
            return _response(ctx, await application.list_documents(context))
        if lowered.startswith("/rag delete "):
            value = command[len("/rag delete ") :].strip()
            try:
                document_id = DocumentId(_system_id(value))
            except ValueError:
                return _response(ctx, "用法：`/rag delete <document_id>`")
            return _response(ctx, await application.delete(context, document_id))
        if lowered == "/rag delete":
            return _response(ctx, "用法：`/rag delete <document_id>`")
        if lowered.startswith("/rag ask "):
            question = command[len("/rag ask ") :].strip()
            if question:
                result = await application.search(context, question)
                if not result.evidence:
                    return _response(
                        ctx,
                        "当前私人知识库没有提供充分依据；未使用其他主体、公共语料或普通附件。",
                    )
                disclosure = (
                    "\n注意：本次检索已降级为仅关键词模式。"
                    if result.status is SearchStatus.LEXICAL_DEGRADED
                    else ""
                )
                ctx.msg.content = (
                    "请仅根据下列私人知识库证据回答用户问题。"
                    "凡是基于证据提出的事实，必须使用证据中的文件名和位置进行引用；"
                    "证据不足时要明确说明，不得编造来源。\n\n"
                    f"用户问题：{question}\n\n"
                    f"{serialize_untrusted_evidence(result.evidence)}"
                    f"{disclosure}"
                )
                return None
        return _response(ctx, "用法：`/rag ask <question>`")

    router.exact("/rag add", dispatch)
    router.exact("/rag status", dispatch)
    router.prefix("/rag status ", dispatch)
    router.exact("/rag list", dispatch)
    router.exact("/rag delete", dispatch)
    router.prefix("/rag delete ", dispatch)
    router.exact("/rag ask", dispatch)
    router.prefix("/rag ask ", dispatch)


def _attachment(value: str) -> IngestionAttachment:
    path = Path(value)
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return IngestionAttachment(path, path.name, mime_type)


def _system_id(value: str) -> str:
    if len(value) != 32 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("invalid system ID")
    return value


def _response(ctx: CommandContext, content: str) -> OutboundMessage:
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "markdown"},
    )


__all__ = [
    "RagApplication",
    "RagCommandApplication",
    "UnavailableRagApplication",
    "register_rag_commands",
]
