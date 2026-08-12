from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from nanobot.bus.events import ConversationScope
from nanobot.rag.chunking import DeterministicChunker, EmbeddingInputBuilder
from nanobot.rag.config import RagChunkingConfig, RagParsingConfig
from nanobot.rag.inference_scheduler import PriorityInferenceScheduler
from nanobot.rag.ingestion import IngestionAttachment, RagIngestionService
from nanobot.rag.lexical import BilingualLexicalAnalyzer, LexicalRepository
from nanobot.rag.local_inference import FakeEmbedder
from nanobot.rag.parser import (
    DocumentFormat,
    ParseCompleteness,
    ParsedBlock,
    ParsedDocument,
    ParseMetrics,
    RagParseError,
)
from nanobot.rag.quota import QuotaManager
from nanobot.rag.store import RagStore
from nanobot.rag.types import (
    JobPhase,
    PrincipalId,
    RagErrorCode,
    RagRequestContext,
    SourceKind,
    SourceLocation,
)
from nanobot.rag.vector_store import VectorGenerationRepository


class WordTokenizer:
    version = "word-v1"

    def encode(self, text: str) -> tuple[str, ...]:
        return tuple(text.split())

    def decode(self, token_ids: tuple[str, ...]) -> str:
        return " ".join(token_ids)


class DiskProbe:
    def free_bytes(self, path: Path) -> int:
        del path
        return 10**9

    def used_bytes(self, path: Path) -> int:
        del path
        return 0


class RecordingParser:
    def __init__(self, *, error: RagParseError | None = None) -> None:
        self.calls: list[Path] = []
        self.error = error

    async def __call__(self, path: Path) -> ParsedDocument:
        self.calls.append(path)
        if self.error is not None:
            raise self.error
        text = path.read_text()
        return ParsedDocument(
            document_format=DocumentFormat.TEXT,
            blocks=(
                ParsedBlock(
                    text,
                    SourceLocation(kind=SourceKind.TEXT_LINES, line_start=1, line_end=1),
                ),
            ),
            completeness=ParseCompleteness.COMPLETE,
            total_chars=len(text),
            source_chars=len(text),
            metrics=ParseMetrics(file_bytes=path.stat().st_size),
        )


def _context() -> RagRequestContext:
    return RagRequestContext(
        principal_id=PrincipalId("a" * 64),
        channel="websocket",
        sender_id="user-1",
        chat_id="chat-1",
        conversation_scope=ConversationScope.PRIVATE,
        authenticated_sender=True,
    )


def _service(
    tmp_path: Path,
    parser: RecordingParser,
) -> tuple[RagIngestionService, RagStore, QuotaManager]:
    store = RagStore.open(tmp_path / "rag", _context().principal_id)
    quota = QuotaManager(
        store,
        per_user_quota_bytes=10**6,
        global_max_bytes=10**9,
        min_free_disk_bytes=0,
        disk_probe=DiskProbe(),
    )
    tokenizer = WordTokenizer()
    embedder = FakeEmbedder(dimension=8, profile_seed="ingestion-test")
    identifiers = iter(f"{number:032x}" for number in range(1, 100))
    service = RagIngestionService(
        store=store,
        quota=quota,
        parser=parser,
        parsing_config=RagParsingConfig(max_file_bytes=10**5),
        chunker=DeterministicChunker(
            RagChunkingConfig(target_tokens=8, overlap_tokens=2, max_sequence_tokens=16),
            tokenizer,
        ),
        input_builder=EmbeddingInputBuilder(tokenizer, max_sequence_tokens=16),
        embedder=embedder,
        vectors=VectorGenerationRepository(store, dimension=8),
        lexical=LexicalRepository(store, BilingualLexicalAnalyzer()),
        clock=lambda: datetime(2026, 8, 12, tzinfo=UTC),
        id_factory=lambda: next(identifiers),
    )
    return service, store, quota


@pytest.mark.asyncio
async def test_accept_batch_returns_persisted_job_without_parsing_or_embedding(
    tmp_path: Path,
) -> None:
    parser = RecordingParser()
    service, store, quota = _service(tmp_path, parser)
    source = tmp_path / "guide.txt"
    source.write_text("local knowledge")

    result = await service.accept_batch(
        _context(),
        (IngestionAttachment(source, "guide.txt", "text/plain"),),
    )

    assert len(result.items) == 1
    assert result.items[0].job_id is not None
    assert result.items[0].duplicate is False
    assert parser.calls == []
    assert quota.usage().reserved_bytes == source.stat().st_size
    assert quota.usage().committed_bytes == 0
    with store.connect() as connection:
        assert connection.execute("SELECT phase FROM jobs").fetchone()[0] == "queued"


@pytest.mark.asyncio
async def test_batch_preflight_is_all_or_none_before_quota_or_document_creation(
    tmp_path: Path,
) -> None:
    parser = RecordingParser()
    service, store, quota = _service(tmp_path, parser)
    valid = tmp_path / "valid.txt"
    invalid = tmp_path / "invalid.exe"
    valid.write_text("valid")
    invalid.write_bytes(b"binary\x00")

    with pytest.raises(RagParseError) as captured:
        await service.accept_batch(
            _context(),
            (
                IngestionAttachment(valid, "valid.txt", "text/plain"),
                IngestionAttachment(invalid, "invalid.exe", "application/octet-stream"),
            ),
        )

    assert captured.value.code is RagErrorCode.UNSUPPORTED_FORMAT
    assert quota.usage().total_bytes == 0
    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_batch_copy_failure_rolls_back_all_documents_jobs_and_reservations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = RecordingParser()
    service, store, quota = _service(tmp_path, parser)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first")
    second.write_text("second")
    original_accept = service.documents.accept_original
    calls = 0

    def fail_second(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated copy failure")
        return original_accept(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(service.documents, "accept_original", fail_second)

    with pytest.raises(OSError, match="copy failure"):
        await service.accept_batch(
            _context(),
            (
                IngestionAttachment(first, "first.txt", "text/plain"),
                IngestionAttachment(second, "second.txt", "text/plain"),
            ),
        )

    assert quota.usage().total_bytes == 0
    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM quota_reservations").fetchone()[0] == 0
    assert not any(store.paths.originals.iterdir())


@pytest.mark.asyncio
async def test_process_job_publishes_complete_generation_and_commits_quota(
    tmp_path: Path,
) -> None:
    parser = RecordingParser()
    service, store, quota = _service(tmp_path, parser)
    source = tmp_path / "guide.txt"
    source.write_text("本地 知识库 uses hybrid retrieval")
    accepted = await service.accept_batch(
        _context(),
        (IngestionAttachment(source, "guide.txt", "text/plain"),),
    )
    job_id = accepted.items[0].job_id
    assert job_id is not None

    job = await service.process_job(job_id)

    assert job.phase is JobPhase.READY
    assert quota.usage().committed_bytes == source.stat().st_size
    assert quota.usage().reserved_bytes == 0
    with store.connect() as connection:
        document = connection.execute(
            "SELECT status, generation_id FROM documents"
        ).fetchone()
        manifest = connection.execute(
            "SELECT active_generation_id FROM store_manifest WHERE singleton = 1"
        ).fetchone()
    assert document[0] == "ready"
    assert document[1] == manifest[0]
    assert service.lexical.search(
        "知识库", generation_id=str(manifest[0]), limit=5
    )[0].document_id == str(accepted.items[0].document_id)
    with service.vectors.pin_active() as pinned:
        assert pinned.generation_id == manifest[0]


@pytest.mark.asyncio
async def test_permanent_parse_failure_removes_partial_state_and_releases_reservation(
    tmp_path: Path,
) -> None:
    parser = RecordingParser(
        error=RagParseError(RagErrorCode.NO_EXTRACTABLE_TEXT, "没有可提取文本")
    )
    service, store, quota = _service(tmp_path, parser)
    source = tmp_path / "empty.txt"
    source.write_text("placeholder")
    accepted = await service.accept_batch(
        _context(),
        (IngestionAttachment(source, "empty.txt", "text/plain"),),
    )
    job_id = accepted.items[0].job_id
    assert job_id is not None

    job = await service.process_job(job_id)

    assert job.phase is JobPhase.FAILED
    assert job.error_code is RagErrorCode.NO_EXTRACTABLE_TEXT
    assert quota.usage().total_bytes == 0
    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
    assert not list(store.paths.originals.rglob("empty.txt"))


@pytest.mark.asyncio
async def test_ingestion_uses_bounded_scheduler_batches(tmp_path: Path) -> None:
    parser = RecordingParser()
    service, _, _ = _service(tmp_path, parser)
    scheduler = PriorityInferenceScheduler()
    calls: list[int] = []
    original_embed = service.embedder.embed_passages

    async def recording_embed(texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        calls.append(len(texts))
        return await original_embed(texts)

    service.embedder.embed_passages = recording_embed  # type: ignore[method-assign]
    service.inference_scheduler = scheduler
    service.embedding_batch_size = 2
    source = tmp_path / "large.txt"
    source.write_text(
        "one two three four five six seven eight nine ten eleven twelve "
        "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty"
    )
    accepted = await service.accept_batch(
        _context(),
        (IngestionAttachment(source, "large.txt", "text/plain"),),
    )
    job_id = accepted.items[0].job_id
    assert job_id is not None

    await scheduler.start()
    await service.process_job(job_id)
    await scheduler.stop()

    assert calls == [2, 1]
