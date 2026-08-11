from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from nanobot.rag.documents import DocumentRepository
from nanobot.rag.quota import QuotaManager
from nanobot.rag.store import RagStore
from nanobot.rag.types import PrincipalId


class FakeDiskProbe:
    def free_bytes(self, path: Path) -> int:
        del path
        return 10**9

    def used_bytes(self, path: Path) -> int:
        del path
        return 0


def _components(root: Path, principal: str) -> tuple[DocumentRepository, QuotaManager]:
    store = RagStore.open(root, PrincipalId(principal * 64))
    quota = QuotaManager(
        store,
        per_user_quota_bytes=10**8,
        global_max_bytes=10**9,
        min_free_disk_bytes=0,
        disk_probe=FakeDiskProbe(),
    )
    return DocumentRepository(store), quota


def _accept(
    repository: DocumentRepository,
    quota: QuotaManager,
    source: Path,
    *,
    document_id: str,
    reservation_id: str,
    display_name: str = "guide.txt",
):
    quota.reserve(reservation_id, source.stat().st_size)
    return repository.accept_original(
        source,
        document_id=document_id,
        job_id=document_id,
        reservation_id=reservation_id,
        display_name=display_name,
        mime_type="text/plain",
    )


def test_original_is_streamed_hashed_and_atomically_published(tmp_path: Path) -> None:
    repository, quota = _components(tmp_path, "a")
    source = tmp_path / "upload.txt"
    content = b"local knowledge\n" * 100_000
    source.write_bytes(content)

    accepted = _accept(
        repository,
        quota,
        source,
        document_id="1" * 32,
        reservation_id="2" * 32,
    )

    assert accepted.duplicate is False
    assert accepted.content_sha256 == hashlib.sha256(content).hexdigest()
    assert accepted.original_bytes == len(content)
    assert accepted.managed_path.read_bytes() == content
    assert accepted.managed_path.stat().st_mode & 0o077 == 0
    assert not list(repository.store.paths.work.rglob("*.part"))
    assert quota.usage().committed_bytes == len(content)
    assert quota.usage().reserved_bytes == 0


def test_same_principal_duplicate_reuses_document_without_double_charge(
    tmp_path: Path,
) -> None:
    repository, quota = _components(tmp_path, "a")
    first_source = tmp_path / "first.txt"
    second_source = tmp_path / "second.txt"
    first_source.write_bytes(b"same")
    second_source.write_bytes(b"same")

    first = _accept(
        repository,
        quota,
        first_source,
        document_id="1" * 32,
        reservation_id="2" * 32,
    )
    duplicate = _accept(
        repository,
        quota,
        second_source,
        document_id="3" * 32,
        reservation_id="4" * 32,
        display_name="renamed.txt",
    )

    assert duplicate.duplicate is True
    assert duplicate.document_id == first.document_id
    assert quota.usage().committed_bytes == 4
    assert not (repository.store.paths.originals / ("3" * 32)).exists()


def test_same_name_different_content_creates_independent_documents(tmp_path: Path) -> None:
    repository, quota = _components(tmp_path, "a")
    first_source = tmp_path / "one" / "same.txt"
    second_source = tmp_path / "two" / "same.txt"
    first_source.parent.mkdir()
    second_source.parent.mkdir()
    first_source.write_bytes(b"one")
    second_source.write_bytes(b"two")

    first = _accept(
        repository,
        quota,
        first_source,
        document_id="1" * 32,
        reservation_id="2" * 32,
        display_name="same.txt",
    )
    second = _accept(
        repository,
        quota,
        second_source,
        document_id="3" * 32,
        reservation_id="4" * 32,
        display_name="same.txt",
    )

    assert first.document_id != second.document_id
    assert quota.usage().committed_bytes == 6


def test_identical_content_is_not_deduplicated_across_principals(tmp_path: Path) -> None:
    first_repository, first_quota = _components(tmp_path, "a")
    second_repository, second_quota = _components(tmp_path, "b")
    source = tmp_path / "shared.txt"
    source.write_bytes(b"same")

    first = _accept(
        first_repository,
        first_quota,
        source,
        document_id="1" * 32,
        reservation_id="2" * 32,
    )
    second = _accept(
        second_repository,
        second_quota,
        source,
        document_id="1" * 32,
        reservation_id="2" * 32,
    )

    assert first.duplicate is False
    assert second.duplicate is False
    assert first.managed_path != second.managed_path


def test_reservation_size_mismatch_does_not_publish_partial_file(tmp_path: Path) -> None:
    repository, quota = _components(tmp_path, "a")
    source = tmp_path / "upload.txt"
    source.write_bytes(b"five!")
    quota.reserve("2" * 32, 4)

    with pytest.raises(ValueError):
        repository.accept_original(
            source,
            document_id="1" * 32,
            job_id="1" * 32,
            reservation_id="2" * 32,
            display_name="upload.txt",
            mime_type="text/plain",
        )

    assert not (repository.store.paths.originals / ("1" * 32)).exists()
    assert quota.usage().reserved_bytes == 4
