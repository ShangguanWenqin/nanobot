from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nanobot.rag.store import (
    SCHEMA_VERSION,
    RagStore,
    StoreSchemaError,
)
from nanobot.rag.types import PrincipalId

PRINCIPAL = PrincipalId("a" * 64)


def test_new_principal_store_creates_versioned_schema_and_manifest(
    tmp_path: Path,
) -> None:
    store = RagStore.open(tmp_path, PRINCIPAL)

    with store.connect() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        manifest = connection.execute(
            "SELECT schema_version, active_generation_id, embedding_profile_id "
            "FROM store_manifest WHERE singleton = 1"
        ).fetchone()

    assert version == SCHEMA_VERSION
    assert {
        "documents",
        "chunks",
        "chunks_fts",
        "jobs",
        "quota_reservations",
        "quota_ledger",
        "store_manifest",
    } <= tables
    assert tuple(manifest) == (SCHEMA_VERSION, None, None)
    assert store.paths.database.stat().st_mode & 0o077 == 0


def test_schema_can_store_chunk_and_matching_fts_row(tmp_path: Path) -> None:
    store = RagStore.open(tmp_path, PRINCIPAL)
    with store.connect() as connection:
        connection.execute(
            "INSERT INTO documents "
            "(document_id, display_name, content_sha256, mime_type, original_bytes, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("1" * 32, "guide.md", "f" * 64, "text/markdown", 10, "ready", 1, 1),
        )
        connection.execute(
            "INSERT INTO chunks "
            "(chunk_key, document_id, ordinal, text, token_count, location_json, "
            "embedding_profile_id, generation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "1" * 32, 0, "本地知识", 4, "{}", "e5-v1", "2" * 32),
        )
        connection.execute(
            "INSERT INTO chunks_fts(rowid, lexical_text) VALUES (?, ?)",
            (1, "本地 知识"),
        )
        hit = connection.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?",
            ("本地",),
        ).fetchone()

    assert hit[0] == 1


def test_future_schema_version_fails_closed(tmp_path: Path) -> None:
    store_dir = tmp_path / "principals" / PRINCIPAL[:2] / PRINCIPAL
    store_dir.mkdir(parents=True)
    database = store_dir / "rag.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")

    with pytest.raises(StoreSchemaError):
        RagStore.open(tmp_path, PRINCIPAL)


def test_managed_paths_reject_untrusted_ids_and_stay_inside_principal(
    tmp_path: Path,
) -> None:
    store = RagStore.open(tmp_path, PRINCIPAL)

    original = store.paths.original_path("1" * 32, "../../报告 final.md")
    work = store.paths.work_path("2" * 32)
    vector = store.paths.vector_path("3" * 32)

    assert original.name == "报告_final.md"
    assert original.is_relative_to(store.paths.principal_root)
    assert work.is_relative_to(store.paths.principal_root)
    assert vector.is_relative_to(store.paths.principal_root)
    with pytest.raises(ValueError):
        store.paths.work_path("../../escape")
    with pytest.raises(ValueError):
        store.paths.vector_path("A" * 32)


def test_symlinked_managed_directory_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    principal_parent = tmp_path / "principals" / PRINCIPAL[:2]
    principal_parent.mkdir(parents=True)
    (principal_parent / PRINCIPAL).symlink_to(outside, target_is_directory=True)

    with pytest.raises(StoreSchemaError):
        RagStore.open(tmp_path, PRINCIPAL)
