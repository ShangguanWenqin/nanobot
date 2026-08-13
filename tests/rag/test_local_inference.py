from __future__ import annotations

import asyncio
import hashlib
import math
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

import nanobot.rag.local_inference as local_inference
from nanobot.rag.local_inference import (
    FakeEmbedder,
    FakeReranker,
    LocalEmbedder,
    LocalReranker,
    create_cpu_session,
    create_onnx_session,
)
from nanobot.rag.model_manifest import (
    LocalModelManifest,
    ModelArtifact,
    canonical_output_sha256,
)
from nanobot.rag.protocols import Embedder, Reranker


def _artifact(path: str) -> ModelArtifact:
    return ModelArtifact(path=path, sha256="a" * 64, bytes=1)


def _manifest(kind: str) -> LocalModelManifest:
    common: dict[str, object] = {
        "kind": kind,
        "repository": "example/local-model",
        "revision": "b" * 40,
        "artifacts": [_artifact("model.onnx"), _artifact("tokenizer.json")],
        "model_path": "model.onnx",
        "tokenizer_path": "tokenizer.json",
        "max_sequence_tokens": 8,
        "normalize": kind == "embedding",
        "precision": "float32",
        "license": "MIT",
        "validation_samples": [
            {"inputs": ["测试"], "expected_output_sha256": "c" * 64}
        ],
    }
    if kind == "embedding":
        common.update({"dimension": 2, "pooling": "attention_mask_mean"})
    else:
        common["acceptance_threshold"] = 0.6
        common["validation_samples"] = [
            {
                "inputs": ["问题", "答案"],
                "expected_output_sha256": "c" * 64,
            }
        ]
    return LocalModelManifest.model_validate(common)


class RecordingTokenizer:
    def __init__(self) -> None:
        self.text_batches: list[tuple[str, ...]] = []
        self.pair_batches: list[tuple[tuple[str, str], ...]] = []

    def encode_texts(
        self,
        texts: tuple[str, ...],
        *,
        max_length: int,
    ) -> dict[str, np.ndarray[Any, Any]]:
        assert max_length == 8
        self.text_batches.append(texts)
        batch = len(texts)
        return {
            "input_ids": np.ones((batch, 3), dtype=np.int64),
            "attention_mask": np.asarray([[1, 1, 0]] * batch, dtype=np.int64),
        }

    def encode_pairs(
        self,
        pairs: tuple[tuple[str, str], ...],
        *,
        max_length: int,
    ) -> dict[str, np.ndarray[Any, Any]]:
        assert max_length == 8
        self.pair_batches.append(pairs)
        return {
            "input_ids": np.ones((len(pairs), 2), dtype=np.int64),
            "attention_mask": np.ones((len(pairs), 2), dtype=np.int64),
        }


class EmbeddingSession:
    def run(
        self,
        output_names: None,
        input_feed: dict[str, np.ndarray[Any, Any]],
    ) -> list[np.ndarray[Any, Any]]:
        del output_names
        batch = input_feed["input_ids"].shape[0]
        hidden = np.asarray([[[3.0, 0.0], [0.0, 4.0], [99.0, 99.0]]] * batch)
        return [hidden]


class RerankerSession:
    def __init__(self) -> None:
        self.offset = 0

    def run(
        self,
        output_names: None,
        input_feed: dict[str, np.ndarray[Any, Any]],
    ) -> list[np.ndarray[Any, Any]]:
        del output_names
        size = input_feed["input_ids"].shape[0]
        all_logits = np.asarray([-2.0, 0.0, 2.0], dtype=np.float32)
        result = all_logits[self.offset : self.offset + size]
        self.offset += size
        return [result.reshape(-1, 1)]


@pytest.mark.asyncio
async def test_local_embedder_applies_e5_prefix_pooling_normalization_and_batches(
    tmp_path: Path,
) -> None:
    tokenizer = RecordingTokenizer()
    runtime = LocalEmbedder(
        _manifest("embedding"),
        tmp_path,
        tokenizer=tokenizer,
        session=EmbeddingSession(),
        batch_size=2,
        max_concurrency=1,
    )

    query = await runtime.embed_query("如何测试")
    passages = await runtime.embed_passages(("甲", "乙", "丙"))

    expected = (0.6, 0.8)
    assert query == pytest.approx(expected)
    assert all(passage == pytest.approx(expected) for passage in passages)
    assert tokenizer.text_batches == [
        ("query: 如何测试",),
        ("passage: 甲", "passage: 乙"),
        ("passage: 丙",),
    ]
    assert runtime.dimension == 2
    assert str(runtime.profile_id) == runtime.manifest.profile_id
    assert isinstance(runtime, Embedder)


@pytest.mark.asyncio
async def test_local_embedder_does_not_duplicate_prebuilt_e5_prefixes(tmp_path: Path) -> None:
    tokenizer = RecordingTokenizer()
    runtime = LocalEmbedder(
        _manifest("embedding"),
        tmp_path,
        tokenizer=tokenizer,
        session=EmbeddingSession(),
    )

    await runtime.embed_query("query: bounded question")
    await runtime.embed_passages(("passage: bounded evidence",))

    assert tokenizer.text_batches == [
        ("query: bounded question",),
        ("passage: bounded evidence",),
    ]


@pytest.mark.asyncio
async def test_local_reranker_pair_scores_are_finite_normalized_and_thresholded(
    tmp_path: Path,
) -> None:
    tokenizer = RecordingTokenizer()
    runtime = LocalReranker(
        _manifest("reranker"),
        tmp_path,
        tokenizer=tokenizer,
        session=RerankerSession(),
        batch_size=2,
        max_concurrency=1,
    )

    scores = await runtime.score("问题", ("甲", "乙", "丙"))

    assert scores == pytest.approx(
        tuple(1.0 / (1.0 + math.exp(-value)) for value in (-2.0, 0.0, 2.0))
    )
    assert tokenizer.pair_batches == [
        (("问题", "甲"), ("问题", "乙")),
        (("问题", "丙"),),
    ]
    assert runtime.acceptance_threshold == 0.6
    assert runtime.accepts(scores[-1]) is True
    assert runtime.accepts(scores[0]) is False
    assert isinstance(runtime, Reranker)


@pytest.mark.asyncio
async def test_fake_inference_implements_same_contract_without_network() -> None:
    embedder = FakeEmbedder(dimension=3, profile_seed="fixture")
    reranker = FakeReranker(acceptance_threshold=0.5, profile_seed="fixture")

    query = await embedder.embed_query("同一输入")
    passages = await embedder.embed_passages(("同一输入", "另一个输入"))
    scores = await reranker.score("问题", ("相关问题", "完全无关"))

    assert query == passages[0]
    assert len(query) == 3
    assert sum(value * value for value in query) == pytest.approx(1.0)
    assert all(math.isfinite(value) for value in (*query, *scores))
    assert all(0.0 <= value <= 1.0 for value in scores)
    assert isinstance(embedder, Embedder)
    assert isinstance(reranker, Reranker)


class BlockingSession(EmbeddingSession):
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.active = 0
        self.maximum_active = 0
        self.lock = threading.Lock()

    def run(
        self,
        output_names: None,
        input_feed: dict[str, np.ndarray[Any, Any]],
    ) -> list[np.ndarray[Any, Any]]:
        with self.lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        self.started.set()
        self.release.wait(timeout=2)
        try:
            return super().run(output_names, input_feed)
        finally:
            with self.lock:
                self.active -= 1


@pytest.mark.asyncio
async def test_inference_is_bounded_and_cancellation_waits_for_local_worker(
    tmp_path: Path,
) -> None:
    session = BlockingSession()
    runtime = LocalEmbedder(
        _manifest("embedding"),
        tmp_path,
        tokenizer=RecordingTokenizer(),
        session=session,
        batch_size=1,
        max_concurrency=1,
    )
    first = asyncio.create_task(runtime.embed_query("一"))
    second = asyncio.create_task(runtime.embed_query("二"))
    await asyncio.to_thread(session.started.wait, 1)

    first.cancel()
    await asyncio.sleep(0.02)
    assert not first.done()
    session.release.set()

    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert session.maximum_active == 1


def test_fake_profile_is_stable_and_content_addressed() -> None:
    first = FakeEmbedder(dimension=3, profile_seed="fixture")
    second = FakeEmbedder(dimension=3, profile_seed="fixture")

    assert first.profile_id == second.profile_id
    assert first.profile_id == hashlib.sha256(b"fake-embedding:fixture:3").hexdigest()


def test_runtime_rejects_wrong_manifest_kind_or_non_local_model_path(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="embedding manifest"):
        LocalEmbedder(
            _manifest("reranker"),
            tmp_path,
            tokenizer=RecordingTokenizer(),
            session=EmbeddingSession(),
        )
    with pytest.raises(ValueError, match="reranker manifest"):
        LocalReranker(
            _manifest("embedding"),
            tmp_path,
            tokenizer=RecordingTokenizer(),
            session=RerankerSession(),
        )


def test_runtime_rejects_invalid_or_non_finite_outputs(tmp_path: Path) -> None:
    class InvalidSession(EmbeddingSession):
        def run(
            self,
            output_names: None,
            input_feed: dict[str, np.ndarray[Any, Any]],
        ) -> list[np.ndarray[Any, Any]]:
            del output_names
            batch = input_feed["input_ids"].shape[0]
            return [np.full((batch, 3, 2), np.nan)]

    runtime = LocalEmbedder(
        _manifest("embedding"),
        tmp_path,
        tokenizer=RecordingTokenizer(),
        session=InvalidSession(),
    )

    with pytest.raises(ValueError, match="finite"):
        asyncio.run(runtime.embed_query("问题"))


def test_real_loader_uses_verified_local_files_and_cpu_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest("embedding")
    (tmp_path / manifest.model_path).write_bytes(b"x")
    (tmp_path / manifest.tokenizer_path).write_bytes(b"x")
    calls: list[tuple[str, object]] = []

    class Backend:
        def token_to_id(self, token: str) -> int | None:
            return 1 if token == "<pad>" else None

        def enable_padding(self, *, pad_id: int, pad_token: str) -> None:
            calls.append(("padding", (pad_id, pad_token)))

        def enable_truncation(self, max_length: int) -> None:
            calls.append(("truncation", max_length))

        def encode_batch(self, inputs: object) -> list[object]:
            del inputs
            return []

    class TokenizerFactory:
        def from_file(self, path: str) -> Backend:
            calls.append(("tokenizer", path))
            return Backend()

    class SessionFactory:
        class Session(EmbeddingSession):
            def get_inputs(self) -> list[object]:
                return [
                    SimpleNamespace(name="input_ids"),
                    SimpleNamespace(name="attention_mask"),
                    SimpleNamespace(name="token_type_ids"),
                ]

        def __call__(self, path: str, *, providers: list[str]) -> Session:
            calls.append(("session", (path, providers)))
            return self.Session()

    modules = {
        "tokenizers": SimpleNamespace(Tokenizer=TokenizerFactory()),
        "onnxruntime": SimpleNamespace(
            InferenceSession=SessionFactory(),
            get_available_providers=lambda: ["CPUExecutionProvider"],
        ),
    }
    monkeypatch.setattr(local_inference, "import_module", modules.__getitem__)

    runtime = LocalEmbedder(manifest, tmp_path)

    assert runtime.profile_id == manifest.profile_id
    assert calls == [
        ("tokenizer", str((tmp_path / manifest.tokenizer_path).resolve())),
        ("truncation", manifest.max_sequence_tokens),
        ("padding", (1, "<pad>")),
        (
            "session",
            (
                str((tmp_path / manifest.model_path).resolve()),
                ["CPUExecutionProvider"],
            ),
        ),
    ]


@pytest.mark.asyncio
async def test_local_failure_is_propagated_without_remote_inference_fallback(
    tmp_path: Path,
) -> None:
    class FailingSession(EmbeddingSession):
        def run(
            self,
            output_names: None,
            input_feed: dict[str, np.ndarray[Any, Any]],
        ) -> list[np.ndarray[Any, Any]]:
            del output_names, input_feed
            raise RuntimeError("local ONNX failure")

    runtime = LocalEmbedder(
        _manifest("embedding"),
        tmp_path,
        tokenizer=RecordingTokenizer(),
        session=FailingSession(),
    )

    with pytest.raises(RuntimeError, match="local ONNX failure"):
        await runtime.embed_query("不会外送")


def test_cpu_session_passes_only_inputs_declared_by_onnx_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[set[str]] = []

    class RawSession:
        def get_inputs(self) -> list[object]:
            return [SimpleNamespace(name="input_ids"), SimpleNamespace(name="attention_mask")]

        def run(self, output_names: None, input_feed: dict[str, object]) -> list[object]:
            del output_names
            received.append(set(input_feed))
            return [np.asarray([[0.0]])]

    class Factory:
        def __call__(self, path: str, *, providers: list[str]) -> RawSession:
            del path, providers
            return RawSession()

    monkeypatch.setattr(
        local_inference,
        "import_module",
        lambda _: SimpleNamespace(
            InferenceSession=Factory(),
            get_available_providers=lambda: ["CPUExecutionProvider"],
        ),
    )
    session = create_cpu_session(tmp_path / "model.onnx")
    session.run(
        None,
        {
            "input_ids": np.asarray([[1]]),
            "attention_mask": np.asarray([[1]]),
            "token_type_ids": np.asarray([[0]]),
        },
    )

    assert received == [{"input_ids", "attention_mask"}]


def test_onnx_session_uses_the_selected_local_execution_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received_providers: list[list[str]] = []

    class RawSession:
        def get_inputs(self) -> list[object]:
            return [SimpleNamespace(name="input_ids"), SimpleNamespace(name="attention_mask")]

        def run(self, output_names: None, input_feed: dict[str, object]) -> list[object]:
            del output_names, input_feed
            return [np.asarray([[0.0]])]

    class Factory:
        def __call__(self, path: str, *, providers: list[str]) -> RawSession:
            del path
            received_providers.append(providers)
            return RawSession()

    monkeypatch.setattr(
        local_inference,
        "import_module",
        lambda _: SimpleNamespace(
            InferenceSession=Factory(),
            get_available_providers=lambda: [
                "CoreMLExecutionProvider",
                "CPUExecutionProvider",
            ],
        ),
    )

    create_onnx_session(tmp_path / "model.onnx", "CoreMLExecutionProvider")

    assert received_providers == [["CoreMLExecutionProvider"]]


def test_onnx_session_rejects_an_unavailable_execution_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_inference,
        "import_module",
        lambda _: SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"]),
    )

    with pytest.raises(ValueError, match="unavailable"):
        create_onnx_session(tmp_path / "model.onnx", "CUDAExecutionProvider")


@pytest.mark.asyncio
async def test_runtime_validates_fixed_manifest_sample_outputs(tmp_path: Path) -> None:
    embedding_manifest = _manifest("embedding")
    embedding_digest = canonical_output_sha256((0.6, 0.8))
    embedding_manifest = embedding_manifest.model_copy(
        update={
            "validation_samples": (
                embedding_manifest.validation_samples[0].model_copy(
                    update={"expected_output_sha256": embedding_digest}
                ),
            )
        }
    )
    embedder = LocalEmbedder(
        embedding_manifest,
        tmp_path,
        tokenizer=RecordingTokenizer(),
        session=EmbeddingSession(),
    )

    assert await embedder.validate_samples() == (embedding_digest,)

    reranker_manifest = _manifest("reranker")
    score_digest = canonical_output_sha256((1.0 / (1.0 + math.exp(2.0)),))
    reranker_manifest = reranker_manifest.model_copy(
        update={
            "validation_samples": (
                reranker_manifest.validation_samples[0].model_copy(
                    update={
                        "inputs": ("问题", "答案"),
                        "expected_output_sha256": score_digest,
                    }
                ),
            )
        }
    )
    reranker = LocalReranker(
        reranker_manifest,
        tmp_path,
        tokenizer=RecordingTokenizer(),
        session=RerankerSession(),
    )

    assert await reranker.validate_samples() == (score_digest,)

    wrong = embedding_manifest.model_copy(
        update={
            "validation_samples": (
                embedding_manifest.validation_samples[0].model_copy(
                    update={"expected_output_sha256": "0" * 64}
                ),
            )
        }
    )
    invalid = LocalEmbedder(
        wrong,
        tmp_path,
        tokenizer=RecordingTokenizer(),
        session=EmbeddingSession(),
    )
    with pytest.raises(ValueError, match="validation sample"):
        await invalid.validate_samples()
