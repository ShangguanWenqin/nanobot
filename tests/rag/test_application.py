from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from nanobot.bus.events import ConversationScope
from nanobot.rag.application import PrincipalRagServices, ServiceBackedRagApplication
from nanobot.rag.ingestion import AcceptedIngestion, AcceptedIngestionBatch
from nanobot.rag.library_status import (
    DocumentPage,
    DocumentSummary,
    LibraryStatus,
    RuntimeProfileReport,
)
from nanobot.rag.progress import RagPhase, RagProgressEvent, RagProgressState
from nanobot.rag.quota import QuotaUsage
from nanobot.rag.types import (
    DocumentId,
    DocumentStatus,
    JobId,
    JobPhase,
    PrincipalId,
    RagRequestContext,
    RagSearchResult,
    SearchStatus,
)


def _context(principal: str = "principal-a") -> RagRequestContext:
    return RagRequestContext(
        principal_id=PrincipalId(principal),
        channel="websocket",
        sender_id="trusted-user",
        chat_id="private-chat",
        conversation_scope=ConversationScope.PRIVATE,
        authenticated_sender=True,
    )


class _Ingestion:
    def __init__(self) -> None:
        self.processed: list[JobId] = []

    async def accept_batch(self, context, attachments):
        del context, attachments
        return AcceptedIngestionBatch(
            items=(
                AcceptedIngestion(
                    document_id=DocumentId("a" * 32),
                    job_id=JobId("b" * 32),
                    duplicate=False,
                ),
            )
        )

    async def process_job(self, job_id, *, on_phase=None):
        self.processed.append(job_id)
        if on_phase is not None:
            await on_phase(JobPhase.PARSING, DocumentId("a" * 32), None)
            await on_phase(JobPhase.READY, DocumentId("a" * 32), None)


class _Deletion:
    def __init__(self) -> None:
        self.processed: list[JobId] = []

    def request_delete(self, context, document_id):
        del context, document_id
        return JobId("c" * 32)

    async def process_job(self, job_id, *, on_phase=None):
        self.processed.append(job_id)
        if on_phase is not None:
            await on_phase(JobPhase.DELETING, None, None)
            await on_phase(JobPhase.READY, None, None)


class _Status:
    def status(self):
        return LibraryStatus(
            quota=QuotaUsage(committed_bytes=10, reserved_bytes=2, quota_bytes=100),
            ready_document_count=1,
            active_jobs=(),
            recent_jobs=(),
            runtime=RuntimeProfileReport(
                query_embedding="cpu",
                batch_embedding="cpu",
                reranker="cpu",
                embedding_profile_id="e5",
                reranker_profile_id="bge",
            ),
        )

    def list_documents(self):
        now = datetime.now(UTC)
        return DocumentPage(
            items=(
                DocumentSummary(
                    document_id=DocumentId("a" * 32),
                    filename="guide.pdf",
                    mime_type="application/pdf",
                    original_bytes=10,
                    status=DocumentStatus.READY,
                    created_at=now,
                    updated_at=now,
                    error_code=None,
                ),
            ),
            next_cursor=None,
        )


class _Retrieval:
    async def search(self, question):
        del question
        return RagSearchResult(status=SearchStatus.NO_EVIDENCE)


@dataclass
class _Attachment:
    source_path: object


@pytest.mark.asyncio
async def test_application_resolves_only_current_principal_and_schedules_jobs() -> None:
    ingestion = _Ingestion()
    deletion = _Deletion()
    observed: list[PrincipalId] = []
    scheduled = []

    def resolve(principal_id):
        observed.append(principal_id)
        return PrincipalRagServices(ingestion, deletion, _Status(), _Retrieval())

    application = ServiceBackedRagApplication(resolve, schedule=scheduled.append)

    added = await application.add(_context(), ())
    deleted = await application.delete(_context(), DocumentId("a" * 32))
    await application.search(_context(), "unknown")
    for awaitable in scheduled:
        await awaitable

    assert observed == [PrincipalId("principal-a")] * 3
    assert "a" * 32 in added and "b" * 32 in added
    assert "c" * 32 in deleted
    assert ingestion.processed == [JobId("b" * 32)]
    assert deletion.processed == [JobId("c" * 32)]


@pytest.mark.asyncio
async def test_application_publishes_ingestion_and_deletion_job_lifecycle() -> None:
    ingestion = _Ingestion()
    deletion = _Deletion()
    services = PrincipalRagServices(ingestion, deletion, _Status(), _Retrieval())
    scheduled = []
    events: list[RagProgressEvent] = []

    async def progress(_context, event):
        events.append(event)

    application = ServiceBackedRagApplication(
        lambda _principal: services,
        schedule=scheduled.append,
        progress=progress,
    )

    await application.add(_context(), ())
    await application.delete(_context(), DocumentId("a" * 32))
    for awaitable in scheduled:
        await awaitable

    assert [(event.operation.value, event.phase.value) for event in events] == [
        ("ingest", "queued"),
        ("delete", "queued"),
        ("ingest", "parsing"),
        ("ingest", "completed"),
        ("delete", "deleting"),
        ("delete", "completed"),
    ]


@pytest.mark.asyncio
async def test_application_formats_path_free_status_and_document_list() -> None:
    services = PrincipalRagServices(_Ingestion(), _Deletion(), _Status(), _Retrieval())
    application = ServiceBackedRagApplication(lambda _principal: services, schedule=lambda _job: None)

    status = await application.status(_context(), None)
    documents = await application.list_documents(_context())

    assert "12 / 100" in status
    assert "cpu" in status
    assert "guide.pdf" in documents
    assert "a" * 32 in documents
    assert "/Users/" not in status + documents


@pytest.mark.asyncio
async def test_query_progress_starts_before_retrieval_and_completes_without_leaking_evidence() -> None:
    order: list[str] = []
    events: list[RagProgressEvent] = []

    class Retrieval:
        async def search(self, question):
            del question
            order.append("retrieval")
            return RagSearchResult(status=SearchStatus.NO_EVIDENCE)

    async def progress(_context, event):
        order.append(event.phase.value)
        events.append(event)

    services = PrincipalRagServices(_Ingestion(), _Deletion(), _Status(), Retrieval())
    application = ServiceBackedRagApplication(
        lambda _principal: services,
        schedule=lambda _job: None,
        progress=progress,
        id_factory=lambda: "d" * 32,
    )

    result = await application.search(_context(), "private question")

    assert result.status is SearchStatus.NO_EVIDENCE
    assert order == ["querying", "retrieval", "completed"]
    assert events[0].state is RagProgressState.RUNNING
    assert events[-1].state is RagProgressState.COMPLETED
    assert events[-1].current == 0 and events[-1].total == 1
    assert "private question" not in str([event.to_public_dict() for event in events])


@pytest.mark.asyncio
async def test_query_progress_discloses_degradation_and_delivery_failure_is_nonfatal() -> None:
    events: list[RagProgressEvent] = []

    class Retrieval:
        async def search(self, question):
            del question
            return RagSearchResult(status=SearchStatus.UNAVAILABLE)

    async def progress(_context, event):
        events.append(event)
        if event.phase is RagPhase.QUERYING:
            raise RuntimeError("notification unavailable")

    services = PrincipalRagServices(_Ingestion(), _Deletion(), _Status(), Retrieval())
    application = ServiceBackedRagApplication(
        lambda _principal: services,
        schedule=lambda _job: None,
        progress=progress,
        id_factory=lambda: "e" * 32,
    )

    result = await application.search(_context(), "question")

    assert result.status is SearchStatus.UNAVAILABLE
    assert events[-1].phase is RagPhase.FAILED
