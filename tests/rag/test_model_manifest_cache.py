from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import pytest
from pydantic import ValidationError

from nanobot.rag.model_cache import HuggingFaceDownloader, ModelCache, ModelCacheError
from nanobot.rag.model_manifest import (
    LocalModelManifest,
    ModelArtifact,
    ModelKind,
    canonical_output_sha256,
)
from nanobot.rag.progress import RagPhase, RagProgressEvent, RagProgressState
from nanobot.rag.types import OperationId, RagErrorCode

MODEL_BYTES = b"fake-onnx-model"
TOKENIZER_BYTES = b'{"version":"1.0"}'


def _artifact(path: str, content: bytes) -> ModelArtifact:
    return ModelArtifact(
        path=path,
        sha256=hashlib.sha256(content).hexdigest(),
        bytes=len(content),
    )


def _embedding_manifest(**overrides: object) -> LocalModelManifest:
    data: dict[str, object] = {
        "kind": "embedding",
        "repository": "example/e5",
        "revision": "a" * 40,
        "artifacts": [
            _artifact("onnx/model.onnx", MODEL_BYTES),
            _artifact("onnx/tokenizer.json", TOKENIZER_BYTES),
        ],
        "model_path": "onnx/model.onnx",
        "tokenizer_path": "onnx/tokenizer.json",
        "dimension": 384,
        "max_sequence_tokens": 512,
        "pooling": "attention_mask_mean",
        "normalize": True,
        "precision": "float32",
        "license": "MIT",
        "trust_remote_code": False,
        "validation_samples": [
            {
                "inputs": ["query: 测试"],
                "expected_output_sha256": "b" * 64,
            }
        ],
    }
    data.update(overrides)
    return LocalModelManifest.model_validate(data)


def test_manifest_is_immutable_versioned_and_profile_is_content_addressed() -> None:
    manifest = _embedding_manifest()

    assert manifest.kind is ModelKind.EMBEDDING
    assert len(manifest.profile_id) == 64
    assert manifest.profile_id == _embedding_manifest().profile_id
    assert manifest.trust_remote_code is False
    assert manifest.artifact("onnx/model.onnx").bytes == len(MODEL_BYTES)


def test_validation_output_digest_is_finite_rounded_and_deterministic() -> None:
    first = canonical_output_sha256((0.12345644, -0.0, 1.0))
    second = canonical_output_sha256((0.12345641, 0.0, 1.0))

    assert first == second
    assert len(first) == 64
    with pytest.raises(ValueError, match="finite"):
        canonical_output_sha256((float("nan"),))


@pytest.mark.parametrize(
    "overrides",
    [
        {"revision": "main"},
        {"trust_remote_code": True},
        {"artifacts": [{"path": "../model.onnx", "sha256": "0" * 64, "bytes": 1}]},
        {"validation_samples": []},
    ],
)
def test_manifest_rejects_mutable_or_unsafe_configuration(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _embedding_manifest(**overrides)


def test_reranker_manifest_requires_calibrated_threshold() -> None:
    data = _embedding_manifest().model_dump()
    data.update(
        {
            "kind": "reranker",
            "dimension": None,
            "pooling": None,
            "normalize": False,
            "acceptance_threshold": None,
        }
    )

    with pytest.raises(ValidationError):
        LocalModelManifest.model_validate(data)


class FakeDownloader:
    def __init__(self, files: dict[str, bytes], *, fail: bool = False) -> None:
        self.files = files
        self.fail = fail
        self.calls = 0
        self._lock = Lock()

    def download(
        self,
        repository: str,
        revision: str,
        remote_path: str,
        destination: Path,
    ) -> None:
        del repository, revision
        with self._lock:
            self.calls += 1
        if self.fail:
            raise OSError("simulated download failure")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.files[remote_path])


def test_cache_downloads_verifies_and_atomically_publishes(tmp_path: Path) -> None:
    manifest = _embedding_manifest()
    downloader = FakeDownloader(
        {
            "onnx/model.onnx": MODEL_BYTES,
            "onnx/tokenizer.json": TOKENIZER_BYTES,
        }
    )
    cache = ModelCache(tmp_path)

    prepared = cache.prepare(manifest, downloader)

    assert prepared.name == manifest.profile_id
    assert cache.verify(manifest) is True
    assert downloader.calls == 2
    assert not list(tmp_path.glob("*.partial-*"))


def test_cache_publishes_model_prepare_lifecycle_without_cache_paths(tmp_path: Path) -> None:
    manifest = _embedding_manifest()
    events: list[RagProgressEvent] = []
    cache = ModelCache(
        tmp_path,
        progress=events.append,
        operation_id_factory=lambda: OperationId("f" * 32),
    )

    cache.prepare(
        manifest,
        FakeDownloader(
            {
                "onnx/model.onnx": MODEL_BYTES,
                "onnx/tokenizer.json": TOKENIZER_BYTES,
            }
        ),
    )

    assert [(event.phase, event.state) for event in events] == [
        (RagPhase.DOWNLOADING, RagProgressState.RUNNING),
        (RagPhase.COMPLETED, RagProgressState.COMPLETED),
    ]
    assert str(tmp_path) not in str([event.to_public_dict() for event in events])


def test_concurrent_prepare_downloads_each_artifact_once(tmp_path: Path) -> None:
    manifest = _embedding_manifest()
    downloader = FakeDownloader(
        {
            "onnx/model.onnx": MODEL_BYTES,
            "onnx/tokenizer.json": TOKENIZER_BYTES,
        }
    )
    cache = ModelCache(tmp_path)

    with ThreadPoolExecutor(max_workers=4) as executor:
        paths = list(executor.map(lambda _: cache.prepare(manifest, downloader), range(4)))

    assert len(set(paths)) == 1
    assert downloader.calls == 2


def test_hash_mismatch_or_download_failure_never_publishes_partial_cache(
    tmp_path: Path,
) -> None:
    manifest = _embedding_manifest()
    bad = FakeDownloader(
        {
            "onnx/model.onnx": b"wrong",
            "onnx/tokenizer.json": TOKENIZER_BYTES,
        }
    )
    cache = ModelCache(tmp_path)

    with pytest.raises(ModelCacheError) as exc_info:
        cache.prepare(manifest, bad)

    assert exc_info.value.code is RagErrorCode.MODEL_INTEGRITY_FAILED
    assert not (tmp_path / manifest.profile_id).exists()
    assert not list(tmp_path.glob("*.partial-*"))


def test_integrity_failure_publishes_safe_terminal_event(tmp_path: Path) -> None:
    events: list[RagProgressEvent] = []
    cache = ModelCache(
        tmp_path,
        progress=events.append,
        operation_id_factory=lambda: OperationId("c" * 32),
    )
    bad = FakeDownloader(
        {
            "onnx/model.onnx": b"wrong",
            "onnx/tokenizer.json": TOKENIZER_BYTES,
        }
    )

    with pytest.raises(ModelCacheError) as exc_info:
        cache.prepare(_embedding_manifest(), bad)

    assert exc_info.value.code is RagErrorCode.MODEL_INTEGRITY_FAILED
    assert events[-1].phase is RagPhase.FAILED
    assert events[-1].error_code is RagErrorCode.MODEL_INTEGRITY_FAILED


def test_offline_missing_cache_has_explicit_error(tmp_path: Path) -> None:
    manifest = _embedding_manifest()
    cache = ModelCache(tmp_path)

    with pytest.raises(ModelCacheError) as exc_info:
        cache.prepare(manifest, FakeDownloader({}), offline=True)

    assert exc_info.value.code is RagErrorCode.MODEL_MISSING


def test_offline_missing_cache_publishes_safe_failed_prepare_event(tmp_path: Path) -> None:
    events: list[RagProgressEvent] = []
    cache = ModelCache(
        tmp_path,
        progress=events.append,
        operation_id_factory=lambda: OperationId("a" * 32),
    )

    with pytest.raises(ModelCacheError) as exc_info:
        cache.prepare(_embedding_manifest(), FakeDownloader({}), offline=True)

    assert exc_info.value.code is RagErrorCode.MODEL_MISSING
    assert events[-1].phase is RagPhase.FAILED
    assert events[-1].state is RagProgressState.FAILED
    assert events[-1].error_code is RagErrorCode.MODEL_MISSING
    assert str(tmp_path) not in str(events[-1].to_public_dict())


def test_admin_prefetch_uses_pinned_huggingface_artifacts(tmp_path: Path) -> None:
    manifest = _embedding_manifest()
    remote_files = {
        "onnx/model.onnx": MODEL_BYTES,
        "onnx/tokenizer.json": TOKENIZER_BYTES,
    }
    calls: list[dict[str, object]] = []

    def fake_hf_download(**kwargs: object) -> str:
        calls.append(kwargs)
        remote_path = str(kwargs["filename"])
        source = tmp_path / "hub" / remote_path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(remote_files[remote_path])
        return str(source)

    cache = ModelCache(tmp_path / "models")
    prepared = cache.prefetch(
        manifest,
        HuggingFaceDownloader(download_function=fake_hf_download),
    )

    assert cache.verify(manifest)
    assert prepared.name == manifest.profile_id
    assert calls == [
        {
            "repo_id": manifest.repository,
            "revision": manifest.revision,
            "filename": artifact.path,
        }
        for artifact in manifest.artifacts
    ]
