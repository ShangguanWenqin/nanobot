from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from nanobot.rag.quota import QuotaManager, RagQuotaError
from nanobot.rag.store import RagStore
from nanobot.rag.types import PrincipalId, RagErrorCode


class FakeDiskProbe:
    def __init__(self, *, free: int = 10_000, used: int = 0) -> None:
        self.free = free
        self.used = used

    def free_bytes(self, path: Path) -> int:
        del path
        return self.free

    def used_bytes(self, path: Path) -> int:
        del path
        return self.used


def _manager(tmp_path: Path, *, limit: int = 100) -> QuotaManager:
    store = RagStore.open(tmp_path, PrincipalId("b" * 64))
    return QuotaManager(
        store,
        per_user_quota_bytes=limit,
        global_max_bytes=10_000,
        min_free_disk_bytes=0,
        disk_probe=FakeDiskProbe(),
    )


def test_concurrent_immediate_reservations_cannot_exceed_quota(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    barrier = Barrier(2)

    def reserve(reservation_id: str) -> bool:
        barrier.wait()
        try:
            manager.reserve(reservation_id, 60)
        except RagQuotaError as exc:
            assert exc.code is RagErrorCode.QUOTA_EXCEEDED
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, ["1" * 32, "2" * 32]))

    assert sorted(results) == [False, True]
    usage = manager.usage()
    assert usage.reserved_bytes == 60
    assert usage.committed_bytes == 0


def test_reservation_is_idempotent_and_commit_transfers_bytes(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    first = manager.reserve("1" * 32, 40)
    second = manager.reserve("1" * 32, 40)
    committed = manager.commit("1" * 32)

    assert first == second
    assert committed.reserved_bytes == 0
    assert committed.committed_bytes == 40


def test_failure_release_and_document_delete_release_usage(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.reserve("1" * 32, 30)
    released = manager.release_reservation("1" * 32)
    manager.reserve("2" * 32, 50)
    manager.commit("2" * 32)
    deleted = manager.release_committed(50)

    assert released.total_bytes == 0
    assert deleted.total_bytes == 0


@pytest.mark.parametrize(
    ("probe", "global_max", "minimum_free"),
    [
        (FakeDiskProbe(used=96), 100, 0),
        (FakeDiskProbe(free=12), 10_000, 10),
    ],
)
def test_global_and_remaining_disk_guards_fail_before_reservation(
    tmp_path: Path,
    probe: FakeDiskProbe,
    global_max: int,
    minimum_free: int,
) -> None:
    store = RagStore.open(tmp_path, PrincipalId("b" * 64))
    manager = QuotaManager(
        store,
        per_user_quota_bytes=100,
        global_max_bytes=global_max,
        min_free_disk_bytes=minimum_free,
        disk_probe=probe,
    )

    with pytest.raises(RagQuotaError) as exc_info:
        manager.reserve("1" * 32, 5)

    assert exc_info.value.code is RagErrorCode.LOW_DISK
    assert manager.usage().total_bytes == 0
