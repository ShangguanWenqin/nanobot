from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from nanobot.bus.events import ConversationScope
from nanobot.rag.chunking import EmbeddingInputBuilder
from nanobot.rag.deletion import RagDeletionService
from nanobot.rag.lexical import BilingualLexicalAnalyzer, LexicalRepository
from nanobot.rag.local_inference import FakeEmbedder
from nanobot.rag.store import RagStore
from nanobot.rag.types import (
    DocumentId,
    JobPhase,
    PrincipalId,
    RagRequestContext,
)
from nanobot.rag.vector_store import VectorGenerationRepository


class WordTokenizer:
    version = "word-v1"

    def encode(self, text: str) -> tuple[str, ...]:
        return tuple(text.split())

    def decode(self, token_ids: tuple[str, ...]) -> str:
        return " ".join(token_ids)


def _context(principal_id: PrincipalId) -> RagRequestContext:
    return RagRequestContext(
        principal_id=principal_id,
        channel="websocket",
        sender_id="user",
        chat_id="chat",
        conversation_scope=ConversationScope.PRIVATE,
        authenticated_sender=True,
    )


def _service(tmp_path: Path) -> tuple[RagDeletionService, RagStore]:
    principal = PrincipalId("a" * 64)
    store = RagStore.open(tmp_path, principal)
    embedder = FakeEmbedder(dimension=8, profile_seed="delete")
    ids = iter(f"{number:032x}" for number in range(10, 100))
    service = RagDeletionService(
        store=store,
        embedder=embedder,
        input_builder=EmbeddingInputBuilder(WordTokenizer(), max_sequence_tokens=16),
        vectors=VectorGenerationRepository(store, dimension=8),
        lexical=LexicalRepository(store, BilingualLexicalAnalyzer()),
        clock=lambda: datetime(2026, 8, 12, tzinfo=UTC),
        id_factory=lambda: next(ids),
    )
    return service, store


def _ready_document(store: RagStore, document_id: str, *, size: int = 4) -> None:
    directory = store.paths.prepare_original_directory(document_id)
    original = directory / "guide.txt"
    original.write_bytes(b"data")
    relative = original.relative_to(store.paths.principal_root).as_posix()
    with store.connect() as connection:
        connection.execute(
            "INSERT INTO documents "
            "(document_id, display_name, content_sha256, mime_type, original_bytes, status, "
            "created_at, updated_at, original_relpath, generation_id) "
            "VALUES (?, ?, ?, ?, ?, 'ready', 1, 1, ?, ?)",
            (document_id, "guide.txt", document_id + document_id, "text/plain", size, relative, "1" * 32),
        )
        connection.execute(
            "INSERT INTO chunks "
            "(chunk_key, document_id, ordinal, text, token_count, location_json, "
            "embedding_profile_id, generation_id) VALUES (?, ?, 0, ?, 2, ?, ?, ?)",
            (
                int(document_id[:8], 16) + 1,
                document_id,
                "local knowledge",
                '{"kind":"text_lines","heading_path":[],"line_end":1,"line_start":1,"page":null,"row_end":null,"row_start":null,"sheet":null,"slide":null}',
                str(FakeEmbedder(dimension=8, profile_seed="delete").profile_id),
                "1" * 32,
            ),
        )
        connection.execute(
            "UPDATE quota_ledger SET committed_bytes = committed_bytes + ? WHERE singleton = 1",
            (size,),
        )


@pytest.mark.asyncio
async def test_delete_request_immediately_excludes_document_then_purges_everything(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    document_id = DocumentId("2" * 32)
    _ready_document(store, str(document_id))

    job_id = service.request_delete(_context(store.paths.principal_id), document_id)

    assert job_id is not None
    with store.connect() as connection:
        assert connection.execute(
            "SELECT status FROM documents WHERE document_id = ?", (str(document_id),)
        ).fetchone()[0] == "deleting"

    job = await service.process_job(job_id)

    assert job.phase is JobPhase.READY
    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM chunks_fts").fetchone()[0] == 0
        assert connection.execute(
            "SELECT committed_bytes FROM quota_ledger WHERE singleton = 1"
        ).fetchone()[0] == 0
    assert not (store.paths.originals / str(document_id)).exists()


def test_unknown_or_other_principal_document_is_indistinguishable_and_unchanged(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path / "owner")
    document_id = DocumentId("2" * 32)
    _ready_document(store, str(document_id))
    other_store = RagStore.open(tmp_path / "other", PrincipalId("b" * 64))
    other_service = RagDeletionService(
        store=other_store,
        embedder=service.embedder,
        input_builder=service.input_builder,
        vectors=VectorGenerationRepository(other_store, dimension=8),
        lexical=LexicalRepository(other_store, BilingualLexicalAnalyzer()),
        clock=lambda: datetime(2026, 8, 12, tzinfo=UTC),
        id_factory=lambda: "9" * 32,
    )

    unknown = service.request_delete(
        _context(store.paths.principal_id), DocumentId("3" * 32)
    )
    cross_principal = other_service.request_delete(
        _context(other_store.paths.principal_id), document_id
    )

    assert unknown is None
    assert cross_principal is None
    with store.connect() as connection:
        assert connection.execute(
            "SELECT status FROM documents WHERE document_id = ?", (str(document_id),)
        ).fetchone()[0] == "ready"


@pytest.mark.asyncio
async def test_original_cleanup_happens_before_quota_release_and_failure_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, store = _service(tmp_path)
    document_id = DocumentId("2" * 32)
    _ready_document(store, str(document_id))
    job_id = service.request_delete(_context(store.paths.principal_id), document_id)
    assert job_id is not None

    def fail_cleanup(target: DocumentId) -> None:
        assert target == document_id
        with store.connect() as connection:
            committed = connection.execute(
                "SELECT committed_bytes FROM quota_ledger WHERE singleton = 1"
            ).fetchone()[0]
        assert committed == 4
        raise OSError("simulated locked original")

    monkeypatch.setattr(service, "_remove_original_directory", fail_cleanup)

    job = await service.process_job(job_id)

    assert job.phase is JobPhase.DELETING
    assert job.attempts == 1
    with store.connect() as connection:
        document = connection.execute(
            "SELECT status FROM documents WHERE document_id = ?", (str(document_id),)
        ).fetchone()
        committed = connection.execute(
            "SELECT committed_bytes FROM quota_ledger WHERE singleton = 1"
        ).fetchone()[0]
    assert document[0] == "deleting"
    assert committed == 4
