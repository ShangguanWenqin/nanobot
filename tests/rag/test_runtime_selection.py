from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nanobot.rag.hardware import RuntimeCandidate, Workload
from nanobot.rag.runtime_selection import (
    BenchmarkObservation,
    BenchmarkPolicy,
    CandidateEvaluation,
    HardwareRuntimeSelector,
    RuntimeFallbackEvent,
    RuntimeSelection,
    RuntimeSelectionCache,
    SafeRuntimeRouter,
)


def _candidate(name: str, provider: str = "CPUExecutionProvider") -> RuntimeCandidate:
    return RuntimeCandidate(
        name=name,
        provider=provider,
        precision="float32",
        workloads=(
            Workload.QUERY_EMBEDDING,
            Workload.BATCH_EMBEDDING,
            Workload.RERANKER,
        ),
    )


CPU = _candidate("cpu-float32")
GPU = _candidate("gpu-float32", "CUDAExecutionProvider")
FAST_BAD = _candidate("fast-but-invalid", "CoreMLExecutionProvider")


def _evaluation(
    candidate: RuntimeCandidate,
    *,
    query_latency: float,
    batch_latency: float,
    reranker_latency: float,
    query_embedding: tuple[float, ...] = (1.0, 0.0),
    batch_embeddings: tuple[tuple[float, ...], ...] = ((1.0, 0.0), (0.0, 1.0)),
    reranker_scores: tuple[float, ...] = (0.9, 0.2),
    stable: bool = True,
    peak_memory_bytes: int = 128,
) -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate=candidate,
        observations=(
            BenchmarkObservation(
                workload=Workload.QUERY_EMBEDDING,
                latency_seconds=query_latency,
                embedding_outputs=(query_embedding,),
                reranker_scores=(),
            ),
            BenchmarkObservation(
                workload=Workload.BATCH_EMBEDDING,
                latency_seconds=batch_latency,
                embedding_outputs=batch_embeddings,
                reranker_scores=(),
            ),
            BenchmarkObservation(
                workload=Workload.RERANKER,
                latency_seconds=reranker_latency,
                embedding_outputs=(),
                reranker_scores=reranker_scores,
            ),
        ),
        stable=stable,
        peak_memory_bytes=peak_memory_bytes,
    )


class FakeEvaluator:
    def __init__(
        self,
        evaluations: dict[str, CandidateEvaluation],
        *,
        delays: dict[str, float] | None = None,
    ) -> None:
        self.evaluations = evaluations
        self.delays = delays or {}
        self.warmed: list[str] = []
        self.evaluated: list[str] = []

    async def warmup(self, candidate: RuntimeCandidate) -> None:
        self.warmed.append(candidate.name)

    async def evaluate(self, candidate: RuntimeCandidate) -> CandidateEvaluation:
        self.evaluated.append(candidate.name)
        await asyncio.sleep(self.delays.get(candidate.name, 0.0))
        return self.evaluations[candidate.name]


@pytest.mark.asyncio
async def test_selector_warms_candidates_and_selects_fastest_per_workload() -> None:
    evaluator = FakeEvaluator(
        {
            CPU.name: _evaluation(
                CPU,
                query_latency=0.010,
                batch_latency=0.080,
                reranker_latency=0.050,
            ),
            GPU.name: _evaluation(
                GPU,
                query_latency=0.020,
                batch_latency=0.015,
                reranker_latency=0.010,
                query_embedding=(0.9999999, 0.0001),
                batch_embeddings=((0.9999999, 0.0001), (0.0001, 0.9999999)),
                reranker_scores=(0.9004, 0.1997),
            ),
        }
    )
    selector = HardwareRuntimeSelector(
        BenchmarkPolicy(total_seconds=1.0, candidate_seconds=0.5),
        evaluator,
    )

    selection = await selector.select("hardware-fingerprint", (CPU, GPU))

    assert evaluator.warmed == [CPU.name, GPU.name]
    assert evaluator.evaluated == [CPU.name, GPU.name]
    assert selection.selected == (
        (Workload.BATCH_EMBEDDING, GPU.name),
        (Workload.QUERY_EMBEDDING, CPU.name),
        (Workload.RERANKER, GPU.name),
    )
    assert selection.ranked(Workload.QUERY_EMBEDDING) == (CPU.name, GPU.name)
    assert selection.ranked(Workload.BATCH_EMBEDDING) == (GPU.name, CPU.name)


@pytest.mark.asyncio
async def test_faster_candidate_fails_correctness_memory_and_stability_gates() -> None:
    cpu_reference = _evaluation(
        CPU,
        query_latency=0.1,
        batch_latency=0.2,
        reranker_latency=0.3,
    )
    cases = (
        _evaluation(
            FAST_BAD,
            query_latency=0.001,
            batch_latency=0.001,
            reranker_latency=0.001,
            query_embedding=(0.0, 1.0),
        ),
        _evaluation(
            FAST_BAD,
            query_latency=0.001,
            batch_latency=0.001,
            reranker_latency=0.001,
            reranker_scores=(0.1, 0.8),
        ),
        _evaluation(
            FAST_BAD,
            query_latency=0.001,
            batch_latency=0.001,
            reranker_latency=0.001,
            peak_memory_bytes=10_000,
        ),
        _evaluation(
            FAST_BAD,
            query_latency=0.001,
            batch_latency=0.001,
            reranker_latency=0.001,
            stable=False,
        ),
    )

    for invalid in cases:
        evaluator = FakeEvaluator({CPU.name: cpu_reference, FAST_BAD.name: invalid})
        selection = await HardwareRuntimeSelector(
            BenchmarkPolicy(
                total_seconds=1.0,
                candidate_seconds=0.5,
                max_peak_memory_bytes=1_000,
            ),
            evaluator,
        ).select("fingerprint", (CPU, FAST_BAD))

        assert selection.selected_candidate(Workload.QUERY_EMBEDDING) == CPU.name
        assert selection.ranked(Workload.RERANKER) == (CPU.name,)
        assert selection.rejections[0][0] == FAST_BAD.name


@pytest.mark.asyncio
async def test_candidate_timeout_is_bounded_and_does_not_block_cpu_selection() -> None:
    evaluator = FakeEvaluator(
        {
            CPU.name: _evaluation(
                CPU,
                query_latency=0.1,
                batch_latency=0.2,
                reranker_latency=0.3,
            ),
            GPU.name: _evaluation(
                GPU,
                query_latency=0.01,
                batch_latency=0.01,
                reranker_latency=0.01,
            ),
        },
        delays={GPU.name: 0.1},
    )

    selection = await HardwareRuntimeSelector(
        BenchmarkPolicy(total_seconds=0.2, candidate_seconds=0.02),
        evaluator,
    ).select("fingerprint", (CPU, GPU))

    assert selection.selected_candidate(Workload.RERANKER) == CPU.name
    assert selection.rejections == ((GPU.name, "candidate_timeout"),)


@pytest.mark.asyncio
async def test_total_budget_stops_evaluating_later_candidates() -> None:
    third = _candidate("third", "OpenVINOExecutionProvider")
    evaluator = FakeEvaluator(
        {
            CPU.name: _evaluation(
                CPU,
                query_latency=0.1,
                batch_latency=0.2,
                reranker_latency=0.3,
            ),
            GPU.name: _evaluation(
                GPU,
                query_latency=0.01,
                batch_latency=0.01,
                reranker_latency=0.01,
            ),
            third.name: _evaluation(
                third,
                query_latency=0.01,
                batch_latency=0.01,
                reranker_latency=0.01,
            ),
        },
        delays={CPU.name: 0.025, GPU.name: 0.025, third.name: 0.025},
    )

    selection = await HardwareRuntimeSelector(
        BenchmarkPolicy(total_seconds=0.04, candidate_seconds=0.03),
        evaluator,
    ).select("fingerprint", (CPU, GPU, third))

    assert third.name not in evaluator.evaluated
    assert selection.rejections[-1] == (third.name, "total_budget_exhausted")


def test_selection_cache_is_atomic_scoped_and_rejects_stale_or_corrupt_data(
    tmp_path: Path,
) -> None:
    selection = RuntimeSelection(
        hardware_fingerprint="fingerprint-a",
        selected=((Workload.QUERY_EMBEDDING, CPU.name),),
        rankings=((Workload.QUERY_EMBEDDING, (CPU.name, GPU.name)),),
        rejections=(),
    )
    cache = RuntimeSelectionCache(tmp_path)

    cache.save(selection)

    assert cache.load("fingerprint-a") == selection
    assert cache.load("fingerprint-b") is None
    assert not list(tmp_path.glob("*.tmp"))
    cache.path_for("fingerprint-a").write_text("{bad json", encoding="utf-8")
    assert cache.load("fingerprint-a") is None


@pytest.mark.asyncio
async def test_selector_reuses_valid_cached_selection_without_benchmark(
    tmp_path: Path,
) -> None:
    cached = RuntimeSelection(
        hardware_fingerprint="fingerprint",
        selected=tuple((workload, CPU.name) for workload in Workload),
        rankings=tuple((workload, (CPU.name,)) for workload in Workload),
        rejections=(),
    )
    cache = RuntimeSelectionCache(tmp_path)
    cache.save(cached)
    evaluator = FakeEvaluator({})

    result = await HardwareRuntimeSelector(
        BenchmarkPolicy(total_seconds=1.0, candidate_seconds=0.5),
        evaluator,
        cache=cache,
    ).select("fingerprint", (CPU,))

    assert result == cached
    assert evaluator.warmed == []


@pytest.mark.asyncio
async def test_safe_router_blacklists_failed_accelerator_emits_once_and_falls_back() -> None:
    selection = RuntimeSelection(
        hardware_fingerprint="fingerprint",
        selected=((Workload.RERANKER, GPU.name),),
        rankings=((Workload.RERANKER, (GPU.name, CPU.name)),),
        rejections=(),
    )
    events: list[RuntimeFallbackEvent] = []
    calls: list[str] = []
    router = SafeRuntimeRouter(selection, event_publisher=events.append)

    async def operation(candidate_name: str) -> str:
        calls.append(candidate_name)
        if candidate_name == GPU.name:
            raise RuntimeError("accelerator OOM")
        return "cpu-result"

    assert await router.execute(Workload.RERANKER, operation) == "cpu-result"
    assert await router.execute(Workload.RERANKER, operation) == "cpu-result"
    assert calls == [GPU.name, CPU.name, CPU.name]
    assert events == [
        RuntimeFallbackEvent(
            workload=Workload.RERANKER,
            failed_candidate=GPU.name,
            fallback_candidate=CPU.name,
            reason="runtime_failure",
        )
    ]
    assert router.blacklisted == (GPU.name,)


@pytest.mark.asyncio
async def test_router_never_uses_unverified_candidate_and_propagates_final_failure() -> None:
    selection = RuntimeSelection(
        hardware_fingerprint="fingerprint",
        selected=((Workload.QUERY_EMBEDDING, CPU.name),),
        rankings=((Workload.QUERY_EMBEDDING, (CPU.name,)),),
        rejections=((GPU.name, "embedding_cosine"),),
    )
    calls: list[str] = []

    async def operation(candidate_name: str) -> str:
        calls.append(candidate_name)
        raise RuntimeError("CPU failed")

    with pytest.raises(RuntimeError, match="all verified local runtime candidates failed"):
        await SafeRuntimeRouter(selection).execute(Workload.QUERY_EMBEDDING, operation)

    assert calls == [CPU.name]


def test_selection_json_does_not_contain_host_paths_or_outputs() -> None:
    selection = RuntimeSelection(
        hardware_fingerprint="fingerprint",
        selected=((Workload.RERANKER, CPU.name),),
        rankings=((Workload.RERANKER, (CPU.name,)),),
        rejections=(),
    )

    payload = json.loads(selection.to_json())

    assert payload == {
        "hardware_fingerprint": "fingerprint",
        "rankings": {"reranker": [CPU.name]},
        "rejections": [],
        "selected": {"reranker": CPU.name},
    }
