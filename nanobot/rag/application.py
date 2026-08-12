"""Application façade that binds private principals to isolated RAG services."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from nanobot.rag.deletion import RagDeletionService
from nanobot.rag.ingestion import IngestionAttachment, RagIngestionService
from nanobot.rag.library_status import LibraryStatusService
from nanobot.rag.progress import (
    RagOperation,
    RagPhase,
    RagProgressEvent,
    RagProgressState,
)
from nanobot.rag.retrieval import HybridRetriever
from nanobot.rag.types import (
    DocumentId,
    JobId,
    OperationId,
    PrincipalId,
    RagErrorCode,
    RagRequestContext,
    RagSearchResult,
    SearchStatus,
)


@dataclass(frozen=True, slots=True)
class PrincipalRagServices:
    ingestion: RagIngestionService
    deletion: RagDeletionService
    status: LibraryStatusService
    retrieval: HybridRetriever


class PrincipalServiceResolver(Protocol):
    def __call__(self, principal_id: PrincipalId) -> PrincipalRagServices: ...


JobScheduler = Callable[[Awaitable[object]], object]
ProgressDelivery = Callable[[RagRequestContext, RagProgressEvent], Awaitable[None]]


class ServiceBackedRagApplication:
    """Route every operation through services resolved from the trusted principal."""

    available = True
    unavailable_message = ""

    def __init__(
        self,
        resolve: PrincipalServiceResolver,
        *,
        schedule: JobScheduler | None = None,
        progress: ProgressDelivery | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._resolve = resolve
        self._schedule = schedule or asyncio.create_task
        self._progress = progress
        self._id_factory = id_factory

    async def add(
        self,
        context: RagRequestContext,
        attachments: tuple[IngestionAttachment, ...],
    ) -> str:
        service = self._resolve(context.principal_id).ingestion
        accepted = await service.accept_batch(context, attachments)
        lines = ["已接受私人知识库入库任务："]
        for item in accepted.items:
            if item.job_id is not None:
                self._schedule(service.process_job(item.job_id))
            state = "已存在，无需重复入库" if item.duplicate else "排队处理中"
            job = str(item.job_id) if item.job_id is not None else "-"
            lines.append(f"- 文档 `{item.document_id}`；任务 `{job}`；{state}")
        return "\n".join(lines)

    async def status(self, context: RagRequestContext, job_id: JobId | None) -> str:
        service = self._resolve(context.principal_id).status
        if job_id is not None:
            job = service.jobs.get(job_id)
            reason = f"；错误 `{job.error_code.value}`" if job.error_code else ""
            return f"任务 `{job.job_id}`：{job.phase.value}{reason}"
        status = service.status()
        quota = status.quota
        return (
            f"配额：{quota.total_bytes} / {quota.quota_bytes} 字节；"
            f"就绪文档：{status.ready_document_count}；活动任务：{len(status.active_jobs)}\n"
            f"运行 Profile：query={status.runtime.query_embedding}，"
            f"batch={status.runtime.batch_embedding}，reranker={status.runtime.reranker}"
        )

    async def list_documents(self, context: RagRequestContext) -> str:
        page = self._resolve(context.principal_id).status.list_documents()
        if not page.items:
            return "当前私人知识库中没有文档。"
        lines = ["私人知识库文档："]
        lines.extend(
            f"- `{item.document_id}` {item.filename}（{item.status.value}，{item.original_bytes} 字节）"
            for item in page.items
        )
        if page.next_cursor is not None:
            lines.append("还有更多文档，请通过管理界面继续查看。")
        return "\n".join(lines)

    async def delete(self, context: RagRequestContext, document_id: DocumentId) -> str:
        service = self._resolve(context.principal_id).deletion
        job_id = service.request_delete(context, document_id)
        if job_id is None:
            return "未找到可删除的文档；其他用户的文档也不会被披露。"
        self._schedule(service.process_job(job_id))
        return f"已隐藏文档 `{document_id}`，后台删除任务为 `{job_id}`。"

    async def search(self, context: RagRequestContext, question: str) -> RagSearchResult:
        operation_id = OperationId(self._new_operation_id())
        await self._notify(
            context,
            RagProgressEvent(
                operation_id=operation_id,
                operation=RagOperation.QUERY,
                phase=RagPhase.QUERYING,
                state=RagProgressState.RUNNING,
                fallback_text="正在从 RAG 知识库中查询…",
            ),
        )
        try:
            result = await self._resolve(context.principal_id).retrieval.search(question)
        except Exception:
            await self._notify(
                context,
                RagProgressEvent(
                    operation_id=operation_id,
                    operation=RagOperation.QUERY,
                    phase=RagPhase.FAILED,
                    state=RagProgressState.FAILED,
                    error_code=RagErrorCode.INTERNAL_ERROR,
                    fallback_text="RAG 查询失败，请稍后重试或查看 `/rag status`。",
                ),
            )
            raise
        if result.status is SearchStatus.UNAVAILABLE:
            await self._notify(
                context,
                RagProgressEvent(
                    operation_id=operation_id,
                    operation=RagOperation.QUERY,
                    phase=RagPhase.FAILED,
                    state=RagProgressState.FAILED,
                    error_code=result.reason or RagErrorCode.INTERNAL_ERROR,
                    fallback_text="RAG 查询当前不可用，请稍后重试或查看 `/rag status`。",
                ),
            )
            return result
        if result.status is SearchStatus.LEXICAL_DEGRADED:
            await self._notify(
                context,
                RagProgressEvent(
                    operation_id=operation_id,
                    operation=RagOperation.QUERY,
                    phase=RagPhase.DEGRADED,
                    state=RagProgressState.RUNNING,
                    fallback_text="RAG 查询已降级为仅关键词模式。",
                ),
            )
        count = len(result.evidence)
        await self._notify(
            context,
            RagProgressEvent(
                operation_id=operation_id,
                operation=RagOperation.QUERY,
                phase=RagPhase.COMPLETED,
                state=RagProgressState.COMPLETED,
                current=count,
                total=max(1, count),
                fallback_text=(
                    f"RAG 查询完成，找到 {count} 条可引用证据。"
                    if count
                    else "RAG 查询完成，但没有找到充分依据。"
                ),
            ),
        )
        return result

    def _new_operation_id(self) -> str:
        if self._id_factory is not None:
            return self._id_factory()
        import secrets

        return secrets.token_hex(16)

    async def _notify(self, context: RagRequestContext, event: RagProgressEvent) -> None:
        if self._progress is None:
            return
        try:
            await self._progress(context, event)
        except Exception:
            # Progress is explicitly best-effort and never authoritative.
            return


__all__ = ["PrincipalRagServices", "ServiceBackedRagApplication"]
