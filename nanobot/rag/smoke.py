"""Reusable correctness gates for real-model RAG release smoke tests."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from nanobot.rag.builtin_models import BGE_RERANKER_BASE, MULTILINGUAL_E5_SMALL
from nanobot.rag.local_inference import LocalEmbedder, LocalReranker

_BENCHMARK_QUERY = "nanobot 如何使用私人知识库？"
_BENCHMARK_PASSAGES = (
    "使用 /rag add 明确把附件加入当前用户的私人知识库。",
    "今天天气晴朗，适合户外活动。",
    "Use /rag ask to query the private knowledge base and receive cited evidence.",
)


@dataclass(frozen=True, slots=True)
class ProviderBenchmark:
    provider: str
    embedding_load_seconds: float
    reranker_load_seconds: float
    query_embedding_seconds: float
    batch_embedding_seconds: float
    reranker_seconds: float
    query_vector: tuple[float, ...]
    passage_vectors: tuple[tuple[float, ...], ...]
    reranker_scores: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("execution provider must not be empty")
        timings = (
            self.embedding_load_seconds,
            self.reranker_load_seconds,
            self.query_embedding_seconds,
            self.batch_embedding_seconds,
            self.reranker_seconds,
        )
        if any(not math.isfinite(value) or value < 0 for value in timings):
            raise ValueError("benchmark timings must be finite and non-negative")
        if not self.query_vector or not self.passage_vectors or not self.reranker_scores:
            raise ValueError("benchmark outputs must not be empty")
        dimension = len(self.query_vector)
        if any(len(vector) != dimension for vector in self.passage_vectors):
            raise ValueError("benchmark embedding dimensions must match")
        values = (
            *self.query_vector,
            *(value for vector in self.passage_vectors for value in vector),
            *self.reranker_scores,
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("benchmark outputs must be finite")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SmokeSelection:
    selected: dict[str, str]
    rejections: dict[str, str]


_PLATFORM_PROVIDERS = {
    "coremlexecutionprovider": (("darwin",), ("arm64", "aarch64")),
    "cudaexecutionprovider": (("linux", "windows"), ("x86_64", "amd64", "arm64", "aarch64")),
    "openvinoexecutionprovider": (("linux", "windows"), ("x86_64", "amd64")),
    "dmlexecutionprovider": (("windows",), ("x86_64", "amd64", "arm64")),
}


def candidate_providers(
    available: tuple[str, ...],
    *,
    os_name: str,
    architecture: str,
) -> tuple[str, ...]:
    """Return the portable CPU baseline plus installed platform-compatible accelerators."""

    installed = set(available)
    if "CPUExecutionProvider" not in installed:
        raise ValueError("CPUExecutionProvider is required for the release baseline")
    result = ["CPUExecutionProvider"]
    normalized_os = os_name.casefold()
    normalized_architecture = architecture.casefold()
    for provider in (
        "CoreMLExecutionProvider",
        "CUDAExecutionProvider",
        "OpenVINOExecutionProvider",
        "DmlExecutionProvider",
    ):
        if provider not in installed:
            continue
        operating_systems, architectures = _PLATFORM_PROVIDERS[provider.casefold()]
        if normalized_os in operating_systems and normalized_architecture in architectures:
            result.append(provider)
    return tuple(result)


def select_fastest_profiles(
    benchmarks: tuple[ProviderBenchmark, ...],
    *,
    embedding_cosine_tolerance: float = 0.999,
    reranker_score_tolerance: float = 0.001,
) -> SmokeSelection:
    """Apply CPU-relative correctness gates, then select the fastest provider per workload."""

    try:
        baseline = next(item for item in benchmarks if item.provider == "CPUExecutionProvider")
    except StopIteration as exc:
        raise ValueError("CPU benchmark is required") from exc
    accepted: list[ProviderBenchmark] = []
    rejections: dict[str, str] = {}
    for benchmark in benchmarks:
        if _matches_baseline(
            baseline,
            benchmark,
            embedding_cosine_tolerance=embedding_cosine_tolerance,
            reranker_score_tolerance=reranker_score_tolerance,
        ):
            accepted.append(benchmark)
        else:
            rejections[benchmark.provider] = "correctness_gate_failed"
    if not accepted:
        raise RuntimeError("no provider passed real-model correctness gates")
    workloads = {
        "query_embedding": "query_embedding_seconds",
        "batch_embedding": "batch_embedding_seconds",
        "reranker": "reranker_seconds",
    }
    selected = {
        workload: min(accepted, key=lambda item: (getattr(item, field), item.provider)).provider
        for workload, field in workloads.items()
    }
    return SmokeSelection(selected=selected, rejections=rejections)


async def benchmark_provider(
    provider: str,
    embedding_dir: Path,
    reranker_dir: Path,
) -> ProviderBenchmark:
    """Run the bounded real-model benchmark shared by startup and release smoke tests."""

    started = time.perf_counter()
    embedder = await asyncio.to_thread(
        LocalEmbedder,
        MULTILINGUAL_E5_SMALL,
        embedding_dir,
        batch_size=len(_BENCHMARK_PASSAGES),
        execution_provider=provider,
    )
    embedding_load = time.perf_counter() - started
    await embedder.embed_query(_BENCHMARK_QUERY)
    await embedder.embed_passages(_BENCHMARK_PASSAGES)
    started = time.perf_counter()
    query_vector = await embedder.embed_query(_BENCHMARK_QUERY)
    query_seconds = time.perf_counter() - started
    started = time.perf_counter()
    passage_vectors = await embedder.embed_passages(_BENCHMARK_PASSAGES)
    batch_seconds = time.perf_counter() - started

    started = time.perf_counter()
    reranker = await asyncio.to_thread(
        LocalReranker,
        BGE_RERANKER_BASE,
        reranker_dir,
        batch_size=len(_BENCHMARK_PASSAGES),
        execution_provider=provider,
    )
    reranker_load = time.perf_counter() - started
    await reranker.score(_BENCHMARK_QUERY, _BENCHMARK_PASSAGES)
    started = time.perf_counter()
    scores = await reranker.score(_BENCHMARK_QUERY, _BENCHMARK_PASSAGES)
    reranker_seconds = time.perf_counter() - started
    return ProviderBenchmark(
        provider=provider,
        embedding_load_seconds=embedding_load,
        reranker_load_seconds=reranker_load,
        query_embedding_seconds=query_seconds,
        batch_embedding_seconds=batch_seconds,
        reranker_seconds=reranker_seconds,
        query_vector=query_vector,
        passage_vectors=passage_vectors,
        reranker_scores=scores,
    )


def _matches_baseline(
    baseline: ProviderBenchmark,
    candidate: ProviderBenchmark,
    *,
    embedding_cosine_tolerance: float,
    reranker_score_tolerance: float,
) -> bool:
    if len(candidate.passage_vectors) != len(baseline.passage_vectors):
        return False
    if len(candidate.reranker_scores) != len(baseline.reranker_scores):
        return False
    embeddings = (
        (baseline.query_vector, candidate.query_vector),
        *zip(baseline.passage_vectors, candidate.passage_vectors, strict=True),
    )
    if any(_cosine(left, right) < embedding_cosine_tolerance for left, right in embeddings):
        return False
    return all(
        abs(left - right) <= reranker_score_tolerance
        for left, right in zip(
            baseline.reranker_scores,
            candidate.reranker_scores,
            strict=True,
        )
    )


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        return -1.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0 or right_norm <= 0:
        return -1.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


__all__ = [
    "ProviderBenchmark",
    "SmokeSelection",
    "benchmark_provider",
    "candidate_providers",
    "select_fastest_profiles",
]
