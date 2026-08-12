"""Immutable USearch generations with transactional manifest activation."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.rag.store import RagStore


class VectorConsistencyError(RuntimeError):
    """Raised when SQLite chunks, vector bytes, and manifest disagree."""


@dataclass(frozen=True, slots=True)
class VectorMatch:
    chunk_key: int
    distance: float


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    rebuild_required: bool
    active_generation_id: str | None
    reason: str | None
    removed_temporary_files: int


class PinnedVectorGeneration:
    def __init__(
        self,
        *,
        generation_id: str,
        embedding_profile_id: str,
        dimension: int,
        index: Any,
        numpy_module: Any,
    ) -> None:
        self.generation_id = generation_id
        self.embedding_profile_id = embedding_profile_id
        self.dimension = dimension
        self._index = index
        self._numpy = numpy_module
        self._closed = False

    def search(self, query: Sequence[float], *, count: int) -> tuple[VectorMatch, ...]:
        if self._closed:
            raise RuntimeError("vector generation pin has been released")
        if len(query) != self.dimension:
            raise ValueError("query vector dimension does not match active generation")
        if count < 1:
            raise ValueError("search count must be positive")
        query_array = self._numpy.asarray(query, dtype=self._numpy.float32)
        matches = self._index.search(query_array, count)
        return tuple(
            VectorMatch(
                chunk_key=int(matches.keys[index]),
                distance=float(matches.distances[index]),
            )
            for index in range(len(matches))
        )

    def release(self) -> None:
        self._closed = True


def _load_vector_dependencies() -> tuple[Any, Any]:
    try:
        import numpy
        from usearch.index import Index
    except ImportError as exc:
        raise RuntimeError("USearch optional dependencies are unavailable") from exc
    return numpy, Index


class VectorGenerationRepository:
    def __init__(self, store: RagStore, *, dimension: int) -> None:
        if dimension < 1:
            raise ValueError("vector dimension must be positive")
        self.store = store
        self.dimension = dimension
        self._reader_counts: dict[str, int] = {}
        self._lock = threading.RLock()

    def set_generation_members(
        self,
        generation_id: str,
        chunk_keys: Sequence[int],
    ) -> None:
        self.store.paths.vector_path(generation_id)
        keys = tuple(chunk_keys)
        if len(keys) != len(set(keys)) or any(key < 0 for key in keys):
            raise ValueError("generation chunk keys must be unique non-negative integers")
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing: set[int] = {
                int(row[0])
                for row in connection.execute(
                    "SELECT chunk_key FROM chunks WHERE chunk_key IN "
                    f"({','.join('?' for _ in keys)})",
                    keys,
                ).fetchall()
            } if keys else set()
            if existing != set(keys):
                raise VectorConsistencyError("generation members must reference existing chunks")
            connection.execute(
                "DELETE FROM generation_chunks WHERE generation_id = ?",
                (generation_id,),
            )
            connection.executemany(
                "INSERT INTO generation_chunks(generation_id, chunk_key) VALUES (?, ?)",
                ((generation_id, key) for key in keys),
            )

    def build_generation(
        self,
        generation_id: str,
        embedding_profile_id: str,
        vectors: Mapping[int, Sequence[float]],
    ) -> Path:
        path = self.store.paths.vector_path(generation_id)
        temporary = path.with_suffix(".tmp")
        with self._lock:
            if path.exists() or temporary.exists():
                raise ValueError("vector generation is immutable and already exists")
            expected_keys = self._sqlite_keys(generation_id)
            vector_keys = set(vectors)
            if vector_keys != expected_keys:
                raise VectorConsistencyError("vector keys do not match SQLite chunk keys")
            if any(len(vector) != self.dimension for vector in vectors.values()):
                raise VectorConsistencyError("vector dimension does not match repository")
            numpy, index_class = _load_vector_dependencies()
            ordered_keys = sorted(vector_keys)
            try:
                index = index_class(ndim=self.dimension, metric="cos", dtype="f32")
                if ordered_keys:
                    index.add(
                        numpy.asarray(ordered_keys, dtype=numpy.uint64),
                        numpy.asarray(
                            [vectors[key] for key in ordered_keys],
                            dtype=numpy.float32,
                        ),
                    )
                index.save(str(temporary))
                self._validate_index_file(temporary, expected_keys)
                os.replace(temporary, path)
                path.chmod(0o600)
                with self.store.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "INSERT INTO vector_generations "
                        "(generation_id, embedding_profile_id, dimension, vector_count, "
                        "state, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            generation_id,
                            embedding_profile_id,
                            self.dimension,
                            len(expected_keys),
                            "built",
                            int(time.time()),
                        ),
                    )
            except BaseException:
                if temporary.exists():
                    temporary.unlink()
                if path.exists():
                    path.unlink()
                raise
            return path

    def activate_generation(
        self,
        generation_id: str,
        embedding_profile_id: str,
        *,
        transaction_callback: Callable[[sqlite3.Connection], None] | None = None,
    ) -> None:
        path = self.store.paths.vector_path(generation_id)
        with self._lock:
            expected_keys = self._sqlite_keys(generation_id)
            with self.store.connect() as connection:
                generation = connection.execute(
                    "SELECT embedding_profile_id, dimension, vector_count "
                    "FROM vector_generations WHERE generation_id = ?",
                    (generation_id,),
                ).fetchone()
            if generation is None:
                raise VectorConsistencyError("vector generation metadata is missing")
            if (
                str(generation[0]) != embedding_profile_id
                or int(generation[1]) != self.dimension
                or int(generation[2]) != len(expected_keys)
            ):
                raise VectorConsistencyError("vector generation metadata is incompatible")
            self._validate_index_file(path, expected_keys)
            with self.store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE vector_generations SET state = 'stale' WHERE state = 'active'"
                )
                connection.execute(
                    "UPDATE vector_generations SET state = 'active' WHERE generation_id = ?",
                    (generation_id,),
                )
                connection.execute(
                    "UPDATE store_manifest SET active_generation_id = ?, "
                    "embedding_profile_id = ?, updated_at = ? WHERE singleton = 1",
                    (generation_id, embedding_profile_id, int(time.time())),
                )
                if transaction_callback is not None:
                    transaction_callback(connection)

    def discard_generation(self, generation_id: str) -> None:
        path = self.store.paths.vector_path(generation_id)
        with self._lock:
            with self.store.connect() as connection:
                row = connection.execute(
                    "SELECT active_generation_id FROM store_manifest WHERE singleton = 1"
                ).fetchone()
                if row is not None and row[0] == generation_id:
                    raise VectorConsistencyError("cannot discard the active vector generation")
                connection.execute(
                    "DELETE FROM vector_generations WHERE generation_id = ?",
                    (generation_id,),
                )
                connection.execute(
                    "DELETE FROM generation_chunks WHERE generation_id = ?",
                    (generation_id,),
                )
            if path.exists():
                if path.is_symlink() or not path.is_file():
                    raise VectorConsistencyError("refusing to discard unsafe vector path")
                path.unlink()

    @contextmanager
    def pin_active(self) -> Generator[PinnedVectorGeneration]:
        with self._lock:
            with self.store.connect() as connection:
                row = connection.execute(
                    "SELECT active_generation_id, embedding_profile_id "
                    "FROM store_manifest WHERE singleton = 1"
                ).fetchone()
            if row is None or row[0] is None or row[1] is None:
                raise VectorConsistencyError("no active vector generation")
            generation_id = str(row[0])
            embedding_profile_id = str(row[1])
            numpy, index_class = _load_vector_dependencies()
            try:
                index = index_class(
                    path=str(self.store.paths.vector_path(generation_id)),
                    view=True,
                )
            except Exception as exc:
                raise VectorConsistencyError("active vector generation cannot be opened") from exc
            if int(index.ndim) != self.dimension:
                raise VectorConsistencyError("active vector dimension is incompatible")
            self._reader_counts[generation_id] = self._reader_counts.get(generation_id, 0) + 1
            pinned = PinnedVectorGeneration(
                generation_id=generation_id,
                embedding_profile_id=embedding_profile_id,
                dimension=self.dimension,
                index=index,
                numpy_module=numpy,
            )
        try:
            yield pinned
        finally:
            with self._lock:
                pinned.release()
                remaining = self._reader_counts[generation_id] - 1
                if remaining:
                    self._reader_counts[generation_id] = remaining
                else:
                    del self._reader_counts[generation_id]

    def collect_stale_generations(self) -> tuple[str, ...]:
        with self._lock:
            with self.store.connect() as connection:
                row = connection.execute(
                    "SELECT active_generation_id FROM store_manifest WHERE singleton = 1"
                ).fetchone()
                active = str(row[0]) if row is not None and row[0] is not None else None
            collected: list[str] = []
            for path in sorted(self.store.paths.vectors.glob("generation-*.usearch")):
                generation_id = path.name.removeprefix("generation-").removesuffix(".usearch")
                try:
                    expected_path = self.store.paths.vector_path(generation_id)
                except ValueError:
                    continue
                if path != expected_path or generation_id == active:
                    continue
                if self._reader_counts.get(generation_id, 0):
                    continue
                path.unlink()
                with self.store.connect() as connection:
                    connection.execute(
                        "UPDATE vector_generations SET state = 'collected' "
                        "WHERE generation_id = ?",
                        (generation_id,),
                    )
                    connection.execute(
                        "DELETE FROM generation_chunks WHERE generation_id = ?",
                        (generation_id,),
                    )
                collected.append(generation_id)
            return tuple(collected)

    def reconcile_startup(self) -> ReconciliationReport:
        """Remove crash artifacts and fail closed on an invalid active generation."""
        with self._lock:
            removed_temporary_files = self._remove_temporary_files()
            with self.store.connect() as connection:
                manifest = connection.execute(
                    "SELECT active_generation_id, embedding_profile_id "
                    "FROM store_manifest WHERE singleton = 1"
                ).fetchone()
                chunk_count = int(
                    connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
                )
            if (
                manifest is None
                or manifest[0] is None
                or manifest[1] is None
            ):
                return ReconciliationReport(
                    rebuild_required=chunk_count > 0,
                    active_generation_id=None,
                    reason="active_generation_missing" if chunk_count > 0 else None,
                    removed_temporary_files=removed_temporary_files,
                )

            generation_id = str(manifest[0])
            embedding_profile_id = str(manifest[1])
            try:
                with self.store.connect() as connection:
                    generation = connection.execute(
                        "SELECT embedding_profile_id, dimension, vector_count "
                        "FROM vector_generations WHERE generation_id = ?",
                        (generation_id,),
                    ).fetchone()
                expected_keys = self._sqlite_keys(generation_id)
                if generation is None:
                    raise VectorConsistencyError("active generation metadata is missing")
                if (
                    str(generation[0]) != embedding_profile_id
                    or int(generation[1]) != self.dimension
                    or int(generation[2]) != len(expected_keys)
                ):
                    raise VectorConsistencyError("active generation metadata is inconsistent")
                self._validate_index_file(
                    self.store.paths.vector_path(generation_id),
                    expected_keys,
                )
            except VectorConsistencyError:
                with self.store.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "UPDATE store_manifest SET active_generation_id = NULL, "
                        "embedding_profile_id = NULL, updated_at = ? WHERE singleton = 1",
                        (int(time.time()),),
                    )
                    connection.execute(
                        "UPDATE vector_generations SET state = 'corrupt' "
                        "WHERE generation_id = ?",
                        (generation_id,),
                    )
                return ReconciliationReport(
                    rebuild_required=True,
                    active_generation_id=None,
                    reason="active_generation_invalid",
                    removed_temporary_files=removed_temporary_files,
                )

            return ReconciliationReport(
                rebuild_required=False,
                active_generation_id=generation_id,
                reason=None,
                removed_temporary_files=removed_temporary_files,
            )

    def _remove_temporary_files(self) -> int:
        removed = 0
        for path in self.store.paths.vectors.glob("generation-*.tmp"):
            generation_id = path.name.removeprefix("generation-").removesuffix(".tmp")
            try:
                expected = self.store.paths.vector_path(generation_id).with_suffix(".tmp")
            except ValueError:
                continue
            if path != expected or path.is_symlink() or not path.is_file():
                continue
            path.unlink()
            removed += 1
        return removed

    def _sqlite_keys(self, generation_id: str) -> set[int]:
        self.store.paths.vector_path(generation_id)
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT chunk_key FROM generation_chunks "
                "WHERE generation_id = ? ORDER BY chunk_key",
                (generation_id,),
            ).fetchall()
        return {int(row[0]) for row in rows}

    def _validate_index_file(self, path: Path, expected_keys: set[int]) -> None:
        if not path.is_file() or path.is_symlink():
            raise VectorConsistencyError("vector generation file is missing or unsafe")
        _, index_class = _load_vector_dependencies()
        try:
            index = index_class(path=str(path), view=True)
            if int(index.ndim) != self.dimension or len(index) != len(expected_keys):
                raise VectorConsistencyError("vector generation shape is inconsistent")
            indexed_keys = {
                int(index.keys[position]) for position in range(len(index.keys))
            }
        except VectorConsistencyError:
            raise
        except Exception as exc:
            raise VectorConsistencyError("vector generation file is corrupt") from exc
        if indexed_keys != expected_keys:
            raise VectorConsistencyError("vector generation keys are inconsistent")


__all__ = [
    "PinnedVectorGeneration",
    "ReconciliationReport",
    "VectorConsistencyError",
    "VectorGenerationRepository",
    "VectorMatch",
]
