"""Transactional original-byte quota accounting for a principal store."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from nanobot.rag.protocols import DiskProbe
from nanobot.rag.store import RagStore
from nanobot.rag.types import RagErrorCode

_RESERVATION_ID = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True, slots=True)
class QuotaUsage:
    committed_bytes: int
    reserved_bytes: int
    quota_bytes: int

    @property
    def total_bytes(self) -> int:
        return self.committed_bytes + self.reserved_bytes

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.quota_bytes - self.total_bytes)


class RagQuotaError(RuntimeError):
    def __init__(self, code: RagErrorCode, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


class QuotaManager:
    def __init__(
        self,
        store: RagStore,
        *,
        per_user_quota_bytes: int,
        global_max_bytes: int,
        min_free_disk_bytes: int,
        disk_probe: DiskProbe,
    ) -> None:
        if per_user_quota_bytes < 1 or global_max_bytes < 1:
            raise ValueError("quota limits must be positive")
        if min_free_disk_bytes < 0:
            raise ValueError("minimum free disk bytes must not be negative")
        self._store = store
        self._quota_bytes = per_user_quota_bytes
        self._global_max_bytes = global_max_bytes
        self._min_free_disk_bytes = min_free_disk_bytes
        self._disk_probe = disk_probe

    def usage(self) -> QuotaUsage:
        with self._store.connect() as connection:
            row = connection.execute(
                "SELECT committed_bytes, reserved_bytes FROM quota_ledger "
                "WHERE singleton = 1"
            ).fetchone()
        return self._usage_from_row(row)

    def reserve(self, reservation_id: str, byte_count: int) -> QuotaUsage:
        self._validate_reservation(reservation_id, byte_count)
        with self._store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT reserved_bytes FROM quota_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if existing is not None:
                if int(existing[0]) != byte_count:
                    raise ValueError("reservation_id already exists with a different size")
                return self._read_usage(connection)

            self._enforce_host_guards(byte_count)
            usage = self._read_usage(connection)
            if usage.total_bytes + byte_count > self._quota_bytes:
                raise RagQuotaError(
                    RagErrorCode.QUOTA_EXCEEDED,
                    "RAG 原始文件配额不足",
                )
            connection.execute(
                "INSERT INTO quota_reservations "
                "(reservation_id, reserved_bytes, created_at) VALUES (?, ?, ?)",
                (reservation_id, byte_count, int(time.time())),
            )
            connection.execute(
                "UPDATE quota_ledger SET reserved_bytes = reserved_bytes + ? "
                "WHERE singleton = 1",
                (byte_count,),
            )
            return self._read_usage(connection)

    def reserve_batch(self, reservations: tuple[tuple[str, int], ...]) -> QuotaUsage:
        if not reservations:
            raise ValueError("quota reservation batch must not be empty")
        if len({reservation_id for reservation_id, _ in reservations}) != len(reservations):
            raise ValueError("quota reservation IDs must be unique within a batch")
        for reservation_id, byte_count in reservations:
            self._validate_reservation(reservation_id, byte_count)
        total = sum(byte_count for _, byte_count in reservations)
        with self._store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in reservations)
            existing = connection.execute(
                f"SELECT reservation_id FROM quota_reservations "
                f"WHERE reservation_id IN ({placeholders})",
                tuple(reservation_id for reservation_id, _ in reservations),
            ).fetchall()
            if existing:
                raise ValueError("quota reservation ID already exists")
            self._enforce_host_guards(total)
            usage = self._read_usage(connection)
            if usage.total_bytes + total > self._quota_bytes:
                raise RagQuotaError(RagErrorCode.QUOTA_EXCEEDED, "RAG 原始文件配额不足")
            created_at = int(time.time())
            connection.executemany(
                "INSERT INTO quota_reservations "
                "(reservation_id, reserved_bytes, created_at) VALUES (?, ?, ?)",
                (
                    (reservation_id, byte_count, created_at)
                    for reservation_id, byte_count in reservations
                ),
            )
            connection.execute(
                "UPDATE quota_ledger SET reserved_bytes = reserved_bytes + ? "
                "WHERE singleton = 1",
                (total,),
            )
            return self._read_usage(connection)

    def commit(self, reservation_id: str) -> QuotaUsage:
        self._validate_reservation_id(reservation_id)
        with self._store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT reserved_bytes FROM quota_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown quota reservation")
            byte_count = int(row[0])
            connection.execute(
                "UPDATE quota_ledger SET "
                "reserved_bytes = reserved_bytes - ?, "
                "committed_bytes = committed_bytes + ? WHERE singleton = 1",
                (byte_count, byte_count),
            )
            connection.execute(
                "DELETE FROM quota_reservations WHERE reservation_id = ?",
                (reservation_id,),
            )
            return self._read_usage(connection)

    def release_reservation(self, reservation_id: str) -> QuotaUsage:
        self._validate_reservation_id(reservation_id)
        with self._store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT reserved_bytes FROM quota_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if row is not None:
                byte_count = int(row[0])
                connection.execute(
                    "UPDATE quota_ledger SET reserved_bytes = reserved_bytes - ? "
                    "WHERE singleton = 1",
                    (byte_count,),
                )
                connection.execute(
                    "DELETE FROM quota_reservations WHERE reservation_id = ?",
                    (reservation_id,),
                )
            return self._read_usage(connection)

    def release_committed(self, byte_count: int) -> QuotaUsage:
        if byte_count < 1:
            raise ValueError("released byte count must be positive")
        with self._store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            usage = self._read_usage(connection)
            if byte_count > usage.committed_bytes:
                raise ValueError("cannot release more committed bytes than are recorded")
            connection.execute(
                "UPDATE quota_ledger SET committed_bytes = committed_bytes - ? "
                "WHERE singleton = 1",
                (byte_count,),
            )
            return self._read_usage(connection)

    def _enforce_host_guards(self, byte_count: int) -> None:
        root = self._store.paths.root
        if self._disk_probe.used_bytes(root) + byte_count > self._global_max_bytes:
            raise RagQuotaError(RagErrorCode.LOW_DISK, "RAG 全局存储上限不足")
        if self._disk_probe.free_bytes(root) - byte_count < self._min_free_disk_bytes:
            raise RagQuotaError(RagErrorCode.LOW_DISK, "主机剩余磁盘空间不足")

    def _read_usage(self, connection: object) -> QuotaUsage:
        import sqlite3

        assert isinstance(connection, sqlite3.Connection)
        row = connection.execute(
            "SELECT committed_bytes, reserved_bytes FROM quota_ledger WHERE singleton = 1"
        ).fetchone()
        return self._usage_from_row(row)

    def _usage_from_row(self, row: object) -> QuotaUsage:
        if row is None:
            raise RuntimeError("quota ledger is missing")
        committed = int(row[0])  # type: ignore[index]
        reserved = int(row[1])  # type: ignore[index]
        return QuotaUsage(committed, reserved, self._quota_bytes)

    @staticmethod
    def _validate_reservation_id(reservation_id: str) -> None:
        if not _RESERVATION_ID.fullmatch(reservation_id):
            raise ValueError("reservation_id must be 32 lowercase hexadecimal characters")

    @classmethod
    def _validate_reservation(cls, reservation_id: str, byte_count: int) -> None:
        cls._validate_reservation_id(reservation_id)
        if byte_count < 1:
            raise ValueError("reserved byte count must be positive")


__all__ = ["QuotaManager", "QuotaUsage", "RagQuotaError"]
