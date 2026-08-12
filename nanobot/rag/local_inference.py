"""Bounded local ONNX embedding and reranking runtimes."""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Callable, Sequence
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

import numpy as np

from nanobot.rag.model_manifest import (
    LocalModelManifest,
    ModelKind,
    canonical_output_sha256,
)
from nanobot.rag.types import EmbeddingProfileId, RerankerProfileId

Array = np.ndarray[Any, Any]
T = TypeVar("T")


class BatchTokenizer(Protocol):
    def encode_texts(
        self,
        texts: tuple[str, ...],
        *,
        max_length: int,
    ) -> dict[str, Array]: ...

    def encode_pairs(
        self,
        pairs: tuple[tuple[str, str], ...],
        *,
        max_length: int,
    ) -> dict[str, Array]: ...


class OnnxSession(Protocol):
    def run(
        self,
        output_names: None,
        input_feed: dict[str, Array],
    ) -> Sequence[Any]: ...


class _OnnxInput(Protocol):
    name: str


class _RawOnnxSession(OnnxSession, Protocol):
    def get_inputs(self) -> Sequence[_OnnxInput]: ...


class _Encoding(Protocol):
    ids: list[int]
    attention_mask: list[int]
    type_ids: list[int]


class _TokenizerBackend(Protocol):
    def token_to_id(self, token: str) -> int | None: ...

    def enable_padding(self, *, pad_id: int, pad_token: str) -> None: ...

    def enable_truncation(self, max_length: int) -> None: ...

    def encode_batch(
        self,
        inputs: list[str] | list[tuple[str, str]],
    ) -> list[_Encoding]: ...


class _TokenizerFactory(Protocol):
    def from_file(self, path: str) -> _TokenizerBackend: ...


class _OnnxSessionFactory(Protocol):
    def __call__(
        self,
        path: str,
        *,
        providers: list[str],
    ) -> _RawOnnxSession: ...


class DeclaredInputSession:
    """Restrict inference input to the names explicitly declared by the ONNX graph."""

    def __init__(self, session: _RawOnnxSession) -> None:
        self._session = session
        self._input_names = frozenset(item.name for item in session.get_inputs())
        required = {"input_ids", "attention_mask"}
        if not required.issubset(self._input_names):
            raise ValueError("ONNX graph does not declare the required tokenizer inputs")

    def run(
        self,
        output_names: None,
        input_feed: dict[str, Array],
    ) -> Sequence[Any]:
        filtered = {
            name: value for name, value in input_feed.items() if name in self._input_names
        }
        missing = self._input_names.difference(filtered)
        if missing:
            raise ValueError(f"tokenizer did not produce ONNX inputs: {sorted(missing)}")
        return self._session.run(output_names, filtered)


class LocalTokenizer:
    """Small adapter around the Rust tokenizers package; never executes model code."""

    def __init__(self, path: Path, *, max_length: int) -> None:
        module = import_module("tokenizers")
        factory = cast(_TokenizerFactory, getattr(module, "Tokenizer"))
        self._backend = factory.from_file(str(path))
        self._backend.enable_truncation(max_length=max_length)
        padding = next(
            (
                (token_id, token)
                for token in ("<pad>", "[PAD]")
                if (token_id := self._backend.token_to_id(token)) is not None
            ),
            None,
        )
        if padding is None:
            raise ValueError("local tokenizer does not define a supported padding token")
        self._backend.enable_padding(pad_id=padding[0], pad_token=padding[1])
        self._max_length = max_length

    def encode_texts(
        self,
        texts: tuple[str, ...],
        *,
        max_length: int,
    ) -> dict[str, Array]:
        self._check_length(max_length)
        return self._to_arrays(self._backend.encode_batch(list(texts)))

    def encode_pairs(
        self,
        pairs: tuple[tuple[str, str], ...],
        *,
        max_length: int,
    ) -> dict[str, Array]:
        self._check_length(max_length)
        return self._to_arrays(self._backend.encode_batch(list(pairs)))

    def _check_length(self, max_length: int) -> None:
        if max_length != self._max_length:
            raise ValueError("tokenizer sequence limit does not match model manifest")

    @staticmethod
    def _to_arrays(encodings: list[_Encoding]) -> dict[str, Array]:
        result = {
            "input_ids": np.asarray([encoding.ids for encoding in encodings], dtype=np.int64),
            "attention_mask": np.asarray(
                [encoding.attention_mask for encoding in encodings], dtype=np.int64
            ),
            "token_type_ids": np.asarray(
                [encoding.type_ids for encoding in encodings], dtype=np.int64
            ),
        }
        return result


def create_cpu_session(model_path: Path) -> OnnxSession:
    module = import_module("onnxruntime")
    factory = cast(_OnnxSessionFactory, getattr(module, "InferenceSession"))
    session = factory(str(model_path), providers=["CPUExecutionProvider"])
    return DeclaredInputSession(session)


def _verified_local_artifact(model_dir: Path, relative_path: str) -> Path:
    root = model_dir.expanduser().resolve()
    artifact = (root / relative_path).resolve()
    if not artifact.is_relative_to(root) or not artifact.is_file() or artifact.is_symlink():
        raise ValueError("model artifact must be a verified local regular file")
    return artifact


class _BoundedRuntime:
    def __init__(self, *, max_concurrency: int) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def _run_local(self, function: Callable[[], T]) -> T:
        async with self._semaphore:
            worker = asyncio.create_task(asyncio.to_thread(function))
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                # A Python thread cannot be safely killed. Keep the capacity reserved
                # until the local call exits, then expose cancellation to the caller.
                await worker
                raise


class LocalEmbedder(_BoundedRuntime):
    def __init__(
        self,
        manifest: LocalModelManifest,
        model_dir: Path,
        *,
        tokenizer: BatchTokenizer | None = None,
        session: OnnxSession | None = None,
        batch_size: int = 32,
        max_concurrency: int = 1,
    ) -> None:
        if manifest.kind is not ModelKind.EMBEDDING:
            raise ValueError("LocalEmbedder requires an embedding manifest")
        if manifest.dimension is None:
            raise ValueError("embedding manifest has no output dimension")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        super().__init__(max_concurrency=max_concurrency)
        self.manifest = manifest
        self.profile_id = EmbeddingProfileId(manifest.profile_id)
        self.dimension = manifest.dimension
        self.batch_size = batch_size
        if tokenizer is None:
            tokenizer_path = _verified_local_artifact(model_dir, manifest.tokenizer_path)
            tokenizer = LocalTokenizer(
                tokenizer_path,
                max_length=manifest.max_sequence_tokens,
            )
        if session is None:
            model_path = _verified_local_artifact(model_dir, manifest.model_path)
            session = create_cpu_session(model_path)
        self._tokenizer = tokenizer
        self._session = session

    async def embed_query(self, text: str) -> tuple[float, ...]:
        if not text.strip():
            raise ValueError("embedding query must not be empty")
        vectors = await self._embed_batch((f"query: {text}",))
        return vectors[0]

    async def embed_passages(
        self,
        texts: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        if any(not text.strip() for text in texts):
            raise ValueError("embedding passages must not be empty")
        results: list[tuple[float, ...]] = []
        prefixed = tuple(f"passage: {text}" for text in texts)
        for start in range(0, len(prefixed), self.batch_size):
            results.extend(await self._embed_batch(prefixed[start : start + self.batch_size]))
        return tuple(results)

    async def validate_samples(self) -> tuple[str, ...]:
        actual: list[str] = []
        for sample in self.manifest.validation_samples:
            digest = canonical_output_sha256(await self.embed_query(sample.inputs[0]))
            if digest != sample.expected_output_sha256:
                raise ValueError("embedding validation sample output does not match manifest")
            actual.append(digest)
        return tuple(actual)

    async def _embed_batch(
        self,
        texts: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        def infer() -> tuple[tuple[float, ...], ...]:
            inputs = self._tokenizer.encode_texts(
                texts,
                max_length=self.manifest.max_sequence_tokens,
            )
            outputs = self._session.run(None, inputs)
            if not outputs:
                raise ValueError("embedding model returned no outputs")
            hidden = np.asarray(outputs[0], dtype=np.float32)
            mask = np.asarray(inputs.get("attention_mask"), dtype=np.float32)
            if hidden.ndim != 3 or mask.ndim != 2 or hidden.shape[:2] != mask.shape:
                raise ValueError("embedding output shape does not match attention mask")
            if hidden.shape[0] != len(texts) or hidden.shape[2] != self.dimension:
                raise ValueError("embedding output dimension does not match manifest")
            weights = mask[..., None]
            counts = weights.sum(axis=1)
            if np.any(counts <= 0):
                raise ValueError("embedding attention mask contains an empty sequence")
            pooled = (hidden * weights).sum(axis=1) / counts
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            if not np.all(np.isfinite(pooled)) or not np.all(np.isfinite(norms)):
                raise ValueError("embedding output must be finite")
            if np.any(norms <= 0):
                raise ValueError("embedding output norm must be positive")
            normalized = pooled / norms
            return tuple(tuple(float(value) for value in row) for row in normalized)

        return await self._run_local(infer)


class LocalReranker(_BoundedRuntime):
    def __init__(
        self,
        manifest: LocalModelManifest,
        model_dir: Path,
        *,
        tokenizer: BatchTokenizer | None = None,
        session: OnnxSession | None = None,
        batch_size: int = 16,
        max_concurrency: int = 1,
    ) -> None:
        if manifest.kind is not ModelKind.RERANKER:
            raise ValueError("LocalReranker requires a reranker manifest")
        if manifest.acceptance_threshold is None:
            raise ValueError("reranker manifest has no acceptance threshold")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        super().__init__(max_concurrency=max_concurrency)
        self.manifest = manifest
        self.profile_id = RerankerProfileId(manifest.profile_id)
        self.acceptance_threshold = manifest.acceptance_threshold
        self.batch_size = batch_size
        if tokenizer is None:
            tokenizer_path = _verified_local_artifact(model_dir, manifest.tokenizer_path)
            tokenizer = LocalTokenizer(
                tokenizer_path,
                max_length=manifest.max_sequence_tokens,
            )
        if session is None:
            model_path = _verified_local_artifact(model_dir, manifest.model_path)
            session = create_cpu_session(model_path)
        self._tokenizer = tokenizer
        self._session = session

    async def score(
        self,
        query: str,
        passages: tuple[str, ...],
    ) -> tuple[float, ...]:
        if not query.strip() or any(not passage.strip() for passage in passages):
            raise ValueError("reranker query and passages must not be empty")
        scores: list[float] = []
        for start in range(0, len(passages), self.batch_size):
            batch = passages[start : start + self.batch_size]
            pairs = tuple((query, passage) for passage in batch)
            scores.extend(await self._score_batch(pairs))
        return tuple(scores)

    def accepts(self, score: float) -> bool:
        return math.isfinite(score) and score >= self.acceptance_threshold

    async def validate_samples(self) -> tuple[str, ...]:
        actual: list[str] = []
        for sample in self.manifest.validation_samples:
            scores = await self.score(sample.inputs[0], (sample.inputs[1],))
            digest = canonical_output_sha256(scores)
            if digest != sample.expected_output_sha256:
                raise ValueError("reranker validation sample output does not match manifest")
            actual.append(digest)
        return tuple(actual)

    async def _score_batch(self, pairs: tuple[tuple[str, str], ...]) -> tuple[float, ...]:
        def infer() -> tuple[float, ...]:
            inputs = self._tokenizer.encode_pairs(
                pairs,
                max_length=self.manifest.max_sequence_tokens,
            )
            outputs = self._session.run(None, inputs)
            if not outputs:
                raise ValueError("reranker model returned no outputs")
            logits = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
            if logits.size != len(pairs) or not np.all(np.isfinite(logits)):
                raise ValueError("reranker output must contain one finite score per pair")
            bounded = np.clip(logits, -60.0, 60.0)
            normalized = 1.0 / (1.0 + np.exp(-bounded))
            return tuple(float(value) for value in normalized)

        return await self._run_local(infer)


class FakeEmbedder:
    def __init__(self, *, dimension: int = 8, profile_seed: str = "default") -> None:
        if dimension < 1:
            raise ValueError("dimension must be positive")
        self.dimension = dimension
        signature = f"fake-embedding:{profile_seed}:{dimension}".encode()
        self.profile_id = EmbeddingProfileId(hashlib.sha256(signature).hexdigest())

    async def embed_query(self, text: str) -> tuple[float, ...]:
        return self._vector(text)

    async def embed_passages(
        self,
        texts: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector(text) for text in texts)

    def _vector(self, text: str) -> tuple[float, ...]:
        if not text.strip():
            raise ValueError("fake embedding input must not be empty")
        values: list[float] = []
        counter = 0
        while len(values) < self.dimension:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            values.extend((byte - 127.5) / 127.5 for byte in digest)
            counter += 1
        selected = values[: self.dimension]
        norm = math.sqrt(sum(value * value for value in selected))
        return tuple(value / norm for value in selected)


class FakeReranker:
    def __init__(
        self,
        *,
        acceptance_threshold: float = 0.5,
        profile_seed: str = "default",
    ) -> None:
        if not math.isfinite(acceptance_threshold):
            raise ValueError("acceptance_threshold must be finite")
        self.acceptance_threshold = acceptance_threshold
        signature = f"fake-reranker:{profile_seed}:{acceptance_threshold}".encode()
        self.profile_id = RerankerProfileId(hashlib.sha256(signature).hexdigest())

    async def score(
        self,
        query: str,
        passages: tuple[str, ...],
    ) -> tuple[float, ...]:
        if not query.strip() or any(not passage.strip() for passage in passages):
            raise ValueError("fake reranker inputs must not be empty")
        query_terms = set(query.casefold())
        return tuple(
            len(query_terms.intersection(set(passage.casefold()))) / max(len(query_terms), 1)
            for passage in passages
        )

    def accepts(self, score: float) -> bool:
        return math.isfinite(score) and score >= self.acceptance_threshold


__all__ = [
    "BatchTokenizer",
    "DeclaredInputSession",
    "FakeEmbedder",
    "FakeReranker",
    "LocalEmbedder",
    "LocalReranker",
    "LocalTokenizer",
    "OnnxSession",
    "create_cpu_session",
]
