from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.rag.store import RagStore
from nanobot.rag.types import PrincipalId
from nanobot.rag.vector_store import VectorConsistencyError, VectorGenerationRepository


def _store(tmp_path: Path) -> RagStore:
    store = RagStore.open(tmp_path, PrincipalId("c" * 64))
    with store.connect() as connection:
        connection.execute(
            "INSERT INTO documents "
            "(document_id, display_name, content_sha256, mime_type, original_bytes, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("1" * 32, "guide.txt", "f" * 64, "text/plain", 4, "ready", 1, 1),
        )
    return store


def _insert_chunks(store: RagStore, generation_id: str, keys: list[int]) -> None:
    with store.connect() as connection:
        for ordinal, key in enumerate(keys):
            connection.execute(
                "INSERT INTO chunks "
                "(chunk_key, document_id, ordinal, text, token_count, location_json, "
                "embedding_profile_id, generation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (key, "1" * 32, ordinal, f"chunk-{key}", 2, "{}", "e5-v1", generation_id),
            )


def test_build_validate_activate_and_search_real_usearch_generation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    generation = "2" * 32
    _insert_chunks(store, generation, [10, 20])
    repository = VectorGenerationRepository(store, dimension=3)

    path = repository.build_generation(
        generation,
        "e5-v1",
        {10: (1.0, 0.0, 0.0), 20: (0.0, 1.0, 0.0)},
    )
    repository.activate_generation(generation, "e5-v1")

    assert path == store.paths.vector_path(generation)
    assert path.is_file()
    with repository.pin_active() as pinned:
        matches = pinned.search((1.0, 0.0, 0.0), count=2)
        assert pinned.generation_id == generation
        assert pinned.embedding_profile_id == "e5-v1"
        assert matches[0].chunk_key == 10
    with store.connect() as connection:
        manifest = connection.execute(
            "SELECT active_generation_id, embedding_profile_id FROM store_manifest "
            "WHERE singleton = 1"
        ).fetchone()
    assert tuple(manifest) == (generation, "e5-v1")


def test_reader_pin_prevents_old_generation_collection(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = "2" * 32
    second = "3" * 32
    _insert_chunks(store, first, [10])
    repository = VectorGenerationRepository(store, dimension=2)
    repository.build_generation(first, "e5-v1", {10: (1.0, 0.0)})
    repository.activate_generation(first, "e5-v1")

    with repository.pin_active():
        with store.connect() as connection:
            connection.execute("DELETE FROM chunks")
        _insert_chunks(store, second, [20])
        repository.build_generation(second, "e5-v1", {20: (0.0, 1.0)})
        repository.activate_generation(second, "e5-v1")
        assert repository.collect_stale_generations() == ()
        assert store.paths.vector_path(first).exists()

    assert repository.collect_stale_generations() == (first,)
    assert not store.paths.vector_path(first).exists()
    assert store.paths.vector_path(second).exists()


def test_vector_and_sqlite_chunk_keys_must_match(tmp_path: Path) -> None:
    store = _store(tmp_path)
    generation = "2" * 32
    _insert_chunks(store, generation, [10, 20])
    repository = VectorGenerationRepository(store, dimension=2)

    with pytest.raises(VectorConsistencyError):
        repository.build_generation(generation, "e5-v1", {10: (1.0, 0.0)})

    assert not store.paths.vector_path(generation).exists()


def test_corrupt_generation_cannot_replace_active_manifest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = "2" * 32
    _insert_chunks(store, first, [10])
    repository = VectorGenerationRepository(store, dimension=2)
    repository.build_generation(first, "e5-v1", {10: (1.0, 0.0)})
    repository.activate_generation(first, "e5-v1")
    corrupt = "3" * 32
    store.paths.vector_path(corrupt).write_bytes(b"not-an-index")

    with pytest.raises(VectorConsistencyError):
        repository.activate_generation(corrupt, "e5-v1")

    with repository.pin_active() as pinned:
        assert pinned.generation_id == first


def test_reconcile_missing_active_vector_requests_safe_rebuild(tmp_path: Path) -> None:
    store = _store(tmp_path)
    generation = "2" * 32
    _insert_chunks(store, generation, [10])
    repository = VectorGenerationRepository(store, dimension=2)
    repository.build_generation(generation, "e5-v1", {10: (1.0, 0.0)})
    repository.activate_generation(generation, "e5-v1")
    store.paths.vector_path(generation).unlink()

    report = repository.reconcile_startup()

    assert report.rebuild_required is True
    assert report.active_generation_id is None
    with store.connect() as connection:
        manifest = connection.execute(
            "SELECT active_generation_id FROM store_manifest WHERE singleton = 1"
        ).fetchone()
        document_count = connection.execute("SELECT count(*) FROM documents").fetchone()[0]
        chunk_count = connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assert manifest[0] is None
    assert document_count == 1
    assert chunk_count == 1


def test_reconcile_corrupt_active_vector_clears_manifest_not_source_data(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    generation = "2" * 32
    _insert_chunks(store, generation, [10])
    repository = VectorGenerationRepository(store, dimension=2)
    repository.build_generation(generation, "e5-v1", {10: (1.0, 0.0)})
    repository.activate_generation(generation, "e5-v1")
    store.paths.vector_path(generation).write_bytes(b"corrupt")

    report = repository.reconcile_startup()

    assert report.rebuild_required is True
    assert report.reason == "active_generation_invalid"
    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 1


def test_reconcile_valid_generation_and_remove_crash_temp(tmp_path: Path) -> None:
    store = _store(tmp_path)
    generation = "2" * 32
    _insert_chunks(store, generation, [10])
    repository = VectorGenerationRepository(store, dimension=2)
    repository.build_generation(generation, "e5-v1", {10: (1.0, 0.0)})
    repository.activate_generation(generation, "e5-v1")
    crash_temp = store.paths.vector_path("3" * 32).with_suffix(".tmp")
    crash_temp.write_bytes(b"partial")

    report = repository.reconcile_startup()

    assert report.rebuild_required is False
    assert report.active_generation_id == generation
    assert report.removed_temporary_files == 1
    assert not crash_temp.exists()
