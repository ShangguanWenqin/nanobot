from __future__ import annotations

from pathlib import Path

from nanobot.rag.lexical import BilingualLexicalAnalyzer, LexicalRepository
from nanobot.rag.store import RagStore
from nanobot.rag.types import PrincipalId


def test_bilingual_analyzer_is_versioned_and_deterministic() -> None:
    analyzer = BilingualLexicalAnalyzer()

    tokens = analyzer.tokens("RAG知识库 v2 配置 config.yaml user_id=ABC_123")

    assert analyzer.version == "nanobot-bilingual-lexical-v1"
    assert "rag" in tokens
    assert "知识" in tokens
    assert "识库" in tokens
    assert "v2" in tokens
    assert "config.yaml" in tokens
    assert "user_id" in tokens
    assert "abc_123" in tokens
    assert tokens == analyzer.tokens("RAG知识库 v2 配置 config.yaml user_id=ABC_123")


def _repository(tmp_path: Path) -> LexicalRepository:
    store = RagStore.open(tmp_path, PrincipalId("d" * 64))
    with store.connect() as connection:
        connection.execute(
            "INSERT INTO documents "
            "(document_id, display_name, content_sha256, mime_type, original_bytes, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("1" * 32, "config.yaml", "f" * 64, "text/plain", 10, "ready", 1, 1),
        )
        chunks = [
            (10, 0, "本地知识库采用混合检索。"),
            (20, 1, "Set User_ID to ABC_123 in config.yaml."),
            (30, 2, "完全无关的文本。"),
        ]
        for key, ordinal, text in chunks:
            connection.execute(
                "INSERT INTO chunks "
                "(chunk_key, document_id, ordinal, text, token_count, location_json, "
                "embedding_profile_id, generation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (key, "1" * 32, ordinal, text, 8, "{}", "e5-v1", "2" * 32),
            )
    return LexicalRepository(store, BilingualLexicalAnalyzer())


def test_fts_indexes_normalized_text_but_returns_original_evidence(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.rebuild_generation("2" * 32)

    chinese = repository.search("知识库", generation_id="2" * 32, limit=10)
    identifier = repository.search(
        "abc_123 config.yaml",
        generation_id="2" * 32,
        limit=10,
    )

    assert chinese[0].chunk_key == 10
    assert chinese[0].text == "本地知识库采用混合检索。"
    assert "知识 识库" not in chinese[0].text
    assert identifier[0].chunk_key == 20


def test_fts_excludes_nonready_documents_and_other_generations(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.rebuild_generation("2" * 32)
    with repository.store.connect() as connection:
        connection.execute(
            "UPDATE documents SET status = 'deleting' WHERE document_id = ?",
            ("1" * 32,),
        )

    assert repository.search("知识库", generation_id="2" * 32, limit=10) == ()
    assert repository.search("知识库", generation_id="3" * 32, limit=10) == ()


def test_empty_or_punctuation_only_query_returns_no_hits(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.rebuild_generation("2" * 32)

    assert repository.search("... ---", generation_id="2" * 32, limit=10) == ()


def test_lexical_generation_membership_can_include_chunk_created_in_older_generation(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    with repository.store.connect() as connection:
        connection.executemany(
            "INSERT INTO generation_chunks (generation_id, chunk_key) VALUES (?, ?)",
            (("3" * 32, 10), ("3" * 32, 20)),
        )

    repository.rebuild_generation("3" * 32)

    assert repository.search(
        "知识库", generation_id="3" * 32, limit=10
    )[0].chunk_key == 10
