"""Original-file publication and same-principal content deduplication."""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path

from nanobot.rag.store import RagStore
from nanobot.rag.types import DocumentId

_COPY_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class AcceptedOriginal:
    document_id: DocumentId
    display_name: str
    mime_type: str
    original_bytes: int
    content_sha256: str
    managed_path: Path
    duplicate: bool


class DocumentRepository:
    def __init__(self, store: RagStore) -> None:
        self.store = store

    def accept_original(
        self,
        source: str | Path,
        *,
        document_id: str,
        job_id: str,
        reservation_id: str,
        display_name: str,
        mime_type: str,
    ) -> AcceptedOriginal:
        source_path = Path(source)
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError("source must be a regular non-symbolic-link file")
        work_directory = self.store.paths.prepare_work_directory(job_id)
        temporary = work_directory / "original.part"
        digest = hashlib.sha256()
        copied_bytes = 0
        try:
            with source_path.open("rb") as source_stream, temporary.open("xb") as target_stream:
                while chunk := source_stream.read(_COPY_CHUNK_BYTES):
                    digest.update(chunk)
                    target_stream.write(chunk)
                    copied_bytes += len(chunk)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            temporary.chmod(0o600)

            accepted = self._publish_or_deduplicate(
                temporary,
                document_id=document_id,
                reservation_id=reservation_id,
                display_name=display_name,
                mime_type=mime_type,
                original_bytes=copied_bytes,
                content_sha256=digest.hexdigest(),
            )
            return accepted
        finally:
            if temporary.exists():
                temporary.unlink()

    def _publish_or_deduplicate(
        self,
        temporary: Path,
        *,
        document_id: str,
        reservation_id: str,
        display_name: str,
        mime_type: str,
        original_bytes: int,
        content_sha256: str,
    ) -> AcceptedOriginal:
        destination: Path | None = None
        try:
            with self.store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                reservation = connection.execute(
                    "SELECT reserved_bytes FROM quota_reservations "
                    "WHERE reservation_id = ?",
                    (reservation_id,),
                ).fetchone()
                if reservation is None:
                    raise ValueError("unknown quota reservation")
                reserved_bytes = int(reservation[0])
                if reserved_bytes != original_bytes:
                    raise ValueError("quota reservation does not match copied original bytes")

                duplicate = connection.execute(
                    "SELECT document_id, display_name, mime_type, original_bytes, "
                    "content_sha256, original_relpath FROM documents "
                    "WHERE content_sha256 = ?",
                    (content_sha256,),
                ).fetchone()
                if duplicate is not None:
                    self._release_reservation(connection, reservation_id, reserved_bytes)
                    managed_path = self.store.paths.principal_root / str(duplicate[5])
                    return AcceptedOriginal(
                        document_id=DocumentId(str(duplicate[0])),
                        display_name=str(duplicate[1]),
                        mime_type=str(duplicate[2]),
                        original_bytes=int(duplicate[3]),
                        content_sha256=str(duplicate[4]),
                        managed_path=managed_path,
                        duplicate=True,
                    )

                self.store.paths.prepare_original_directory(document_id)
                destination = self.store.paths.original_path(document_id, display_name)
                if destination.exists() or destination.is_symlink():
                    raise ValueError("managed original destination already exists")
                os.replace(temporary, destination)
                destination.chmod(0o600)
                timestamp = int(time.time())
                relative_path = destination.relative_to(
                    self.store.paths.principal_root
                ).as_posix()
                connection.execute(
                    "INSERT INTO documents "
                    "(document_id, display_name, content_sha256, mime_type, original_bytes, "
                    "status, created_at, updated_at, original_relpath) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        document_id,
                        destination.name,
                        content_sha256,
                        mime_type,
                        original_bytes,
                        "processing",
                        timestamp,
                        timestamp,
                        relative_path,
                    ),
                )
                self._commit_reservation(connection, reservation_id, reserved_bytes)

            return AcceptedOriginal(
                document_id=DocumentId(document_id),
                display_name=destination.name,
                mime_type=mime_type,
                original_bytes=original_bytes,
                content_sha256=content_sha256,
                managed_path=destination,
                duplicate=False,
            )
        except BaseException:
            if destination is not None and destination.exists():
                destination.unlink()
            raise

    @staticmethod
    def _release_reservation(
        connection: object,
        reservation_id: str,
        reserved_bytes: int,
    ) -> None:
        import sqlite3

        assert isinstance(connection, sqlite3.Connection)
        connection.execute(
            "UPDATE quota_ledger SET reserved_bytes = reserved_bytes - ? "
            "WHERE singleton = 1",
            (reserved_bytes,),
        )
        connection.execute(
            "DELETE FROM quota_reservations WHERE reservation_id = ?",
            (reservation_id,),
        )

    @staticmethod
    def _commit_reservation(
        connection: object,
        reservation_id: str,
        reserved_bytes: int,
    ) -> None:
        import sqlite3

        assert isinstance(connection, sqlite3.Connection)
        connection.execute(
            "UPDATE quota_ledger SET "
            "reserved_bytes = reserved_bytes - ?, "
            "committed_bytes = committed_bytes + ? WHERE singleton = 1",
            (reserved_bytes, reserved_bytes),
        )
        connection.execute(
            "DELETE FROM quota_reservations WHERE reservation_id = ?",
            (reservation_id,),
        )


__all__ = ["AcceptedOriginal", "DocumentRepository"]
