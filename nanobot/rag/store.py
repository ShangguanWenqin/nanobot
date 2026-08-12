"""Per-principal SQLite schema and path-safe managed storage."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from nanobot.rag.identity import principal_directory_name
from nanobot.rag.types import PrincipalId

SCHEMA_VERSION = 2
_SYSTEM_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_UNSAFE_FILENAME = re.compile(r"[^\w.()\-]+", flags=re.UNICODE)


class StoreSchemaError(RuntimeError):
    """Raised when managed layout or schema invariants cannot be established."""


def _validate_system_id(value: str, *, name: str) -> str:
    if not _SYSTEM_ID_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be 32 lowercase hexadecimal characters")
    return value


def _safe_display_filename(value: str) -> str:
    basename = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
    sanitized = _UNSAFE_FILENAME.sub("_", basename).strip(" ._")
    if not sanitized:
        sanitized = "document"
    return sanitized[:180]


def _ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise StoreSchemaError("managed RAG directory must not be a symbolic link")
    path.mkdir(parents=False, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise StoreSchemaError("managed RAG path is not a private directory")
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise StoreSchemaError("failed to secure managed RAG directory") from exc


@dataclass(frozen=True, slots=True)
class RagStorePaths:
    root: Path
    principal_id: PrincipalId
    principal_root: Path
    database: Path
    originals: Path
    vectors: Path
    work: Path

    @classmethod
    def create(cls, root: str | Path, principal_id: PrincipalId) -> "RagStorePaths":
        principal_directory_name(principal_id)
        root_path = Path(root).expanduser().absolute()
        if root_path.exists() and root_path.is_symlink():
            raise StoreSchemaError("RAG root must not be a symbolic link")
        root_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            root_path.chmod(0o700)
        except OSError as exc:
            raise StoreSchemaError("failed to secure RAG root") from exc

        principals = root_path / "principals"
        _ensure_private_directory(principals)
        shard = principals / str(principal_id)[:2]
        _ensure_private_directory(shard)
        principal_root = shard / str(principal_id)
        _ensure_private_directory(principal_root)
        originals = principal_root / "originals"
        vectors = principal_root / "vectors"
        work = principal_root / "work"
        for directory in (originals, vectors, work):
            _ensure_private_directory(directory)
        return cls(
            root=root_path,
            principal_id=principal_id,
            principal_root=principal_root,
            database=principal_root / "rag.sqlite3",
            originals=originals,
            vectors=vectors,
            work=work,
        )

    def original_path(self, document_id: str, display_name: str) -> Path:
        identifier = _validate_system_id(document_id, name="document_id")
        return self.originals / identifier / _safe_display_filename(display_name)

    def prepare_original_directory(self, document_id: str) -> Path:
        identifier = _validate_system_id(document_id, name="document_id")
        directory = self.originals / identifier
        _ensure_private_directory(directory)
        return directory

    def work_path(self, job_id: str) -> Path:
        return self.work / _validate_system_id(job_id, name="job_id")

    def prepare_work_directory(self, job_id: str) -> Path:
        directory = self.work_path(job_id)
        _ensure_private_directory(directory)
        return directory

    def vector_path(self, generation_id: str) -> Path:
        identifier = _validate_system_id(generation_id, name="generation_id")
        return self.vectors / f"generation-{identifier}.usearch"


_SCHEMA_SQL = """
CREATE TABLE documents (
    document_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    content_sha256 TEXT NOT NULL UNIQUE,
    mime_type TEXT NOT NULL,
    original_bytes INTEGER NOT NULL CHECK (original_bytes >= 0),
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    error_code TEXT,
    original_relpath TEXT,
    generation_id TEXT
);

CREATE TABLE chunks (
    chunk_key INTEGER PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    text TEXT NOT NULL,
    token_count INTEGER NOT NULL CHECK (token_count >= 0),
    location_json TEXT NOT NULL,
    embedding_profile_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    UNIQUE(document_id, ordinal, generation_id)
);

CREATE TABLE generation_chunks (
    generation_id TEXT NOT NULL,
    chunk_key INTEGER NOT NULL REFERENCES chunks(chunk_key) ON DELETE CASCADE,
    PRIMARY KEY(generation_id, chunk_key)
);

CREATE TRIGGER chunks_generation_membership_after_insert
AFTER INSERT ON chunks
BEGIN
    INSERT OR IGNORE INTO generation_chunks(generation_id, chunk_key)
    VALUES (NEW.generation_id, NEW.chunk_key);
END;

CREATE TABLE vector_generations (
    generation_id TEXT PRIMARY KEY,
    embedding_profile_id TEXT NOT NULL,
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    vector_count INTEGER NOT NULL CHECK (vector_count >= 0),
    state TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
    lexical_text,
    tokenize = 'unicode61'
);

CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    phase TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    document_id TEXT REFERENCES documents(document_id),
    reservation_id TEXT,
    channel TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    error_code TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE quota_reservations (
    reservation_id TEXT PRIMARY KEY,
    job_id TEXT,
    reserved_bytes INTEGER NOT NULL CHECK (reserved_bytes > 0),
    created_at INTEGER NOT NULL
);

CREATE TABLE quota_ledger (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    committed_bytes INTEGER NOT NULL DEFAULT 0 CHECK (committed_bytes >= 0),
    reserved_bytes INTEGER NOT NULL DEFAULT 0 CHECK (reserved_bytes >= 0)
);

CREATE TABLE store_manifest (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL,
    active_generation_id TEXT,
    embedding_profile_id TEXT,
    lexical_analyzer_version TEXT,
    chunking_version TEXT,
    updated_at INTEGER NOT NULL
);

INSERT INTO quota_ledger(singleton, committed_bytes, reserved_bytes) VALUES (1, 0, 0);
INSERT INTO store_manifest(
    singleton, schema_version, active_generation_id, embedding_profile_id,
    lexical_analyzer_version, chunking_version, updated_at
) VALUES (1, 2, NULL, NULL, NULL, NULL, 0);
PRAGMA user_version = 2;
"""

_MIGRATE_V1_TO_V2_SQL = """
CREATE TABLE generation_chunks (
    generation_id TEXT NOT NULL,
    chunk_key INTEGER NOT NULL REFERENCES chunks(chunk_key) ON DELETE CASCADE,
    PRIMARY KEY(generation_id, chunk_key)
);

INSERT INTO generation_chunks(generation_id, chunk_key)
SELECT generation_id, chunk_key FROM chunks;

CREATE TRIGGER chunks_generation_membership_after_insert
AFTER INSERT ON chunks
BEGIN
    INSERT OR IGNORE INTO generation_chunks(generation_id, chunk_key)
    VALUES (NEW.generation_id, NEW.chunk_key);
END;

UPDATE store_manifest SET schema_version = 2 WHERE singleton = 1;
PRAGMA user_version = 2;
"""


class RagStore:
    """A physical store belonging to exactly one authenticated principal."""

    def __init__(self, paths: RagStorePaths) -> None:
        self.paths = paths

    @classmethod
    def open(cls, root: str | Path, principal_id: PrincipalId) -> "RagStore":
        store = cls(RagStorePaths.create(root, principal_id))
        store._migrate()
        return store

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.paths.database, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _migrate(self) -> None:
        try:
            with self._new_connection() as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version > SCHEMA_VERSION:
                    raise StoreSchemaError("RAG store schema is newer than this runtime")
                if version == 0:
                    connection.executescript(_SCHEMA_SQL)
                elif version == 1:
                    connection.executescript(_MIGRATE_V1_TO_V2_SQL)
                elif version < SCHEMA_VERSION:
                    raise StoreSchemaError("RAG store requires an unavailable migration")
                connection.execute("PRAGMA journal_mode = WAL")
            self.paths.database.chmod(0o600)
        except StoreSchemaError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise StoreSchemaError("failed to initialize RAG store") from exc

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection]:
        connection = self._new_connection()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


__all__ = [
    "RagStore",
    "RagStorePaths",
    "SCHEMA_VERSION",
    "StoreSchemaError",
]
