"""Correctness-gated local runtime benchmarking, caching, and safe fallback."""

from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar, cast

from nanobot.rag.hardware import RuntimeCandidate, Workload
from nanobot.rag.progress import (
    RagOperation,
    RagPhase,
    RagProgressEvent,
    RagProgressState,
    runtime_fallback_progress_event,
)
from nanobot.rag.types import OperationId, RagErrorCode

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class BenchmarkPolicy:
    total_seconds: float = 60.0
    candidate_seconds: float = 10.0
    embedding_cosine_tolerance: float = 0.999
    reranker_score_tolerance: float = 0.001
    max_peak_memory_bytes: int | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.total_seconds) or self.total_seconds <= 0:
            raise ValueError("total benchmark budget must be finite and positive")
        if not math.isfinite(self.candidate_seconds) or self.candidate_seconds <= 0:
            raise ValueError("candidate benchmark budget must be finite and positive")
        if self.candidate_seconds > self.total_seconds:
            raise ValueError("candidate benchmark budget must not exceed total budget")
        if not -1.0 <= self.embedding_cosine_tolerance <= 1.0:
            raise ValueError("embedding cosine tolerance must be between -1 and 1")
        if self.reranker_score_tolerance < 0 or not math.isfinite(
            self.reranker_score_tolerance
        ):
            raise ValueError("reranker score tolerance must be finite and non-negative")
        if self.max_peak_memory_bytes is not None and self.max_peak_memory_bytes < 1:
            raise ValueError("peak memory limit must be positive")


@dataclass(frozen=True, slots=True)
class BenchmarkObservation:
    workload: Workload
    latency_seconds: float
    embedding_outputs: tuple[tuple[float, ...], ...]
    reranker_scores: tuple[float, ...]

    def __post_init__(self) -> None:
        if not math.isfinite(self.latency_seconds) or self.latency_seconds < 0:
            raise ValueError("benchmark latency must be finite and non-negative")
        if self.workload is Workload.RERANKER:
            if self.embedding_outputs or not self.reranker_scores:
                raise ValueError("reranker observation requires only reranker scores")
        elif not self.embedding_outputs or self.reranker_scores:
            raise ValueError("embedding observation requires only embedding outputs")
        values = (
            *(value for vector in self.embedding_outputs for value in vector),
            *self.reranker_scores,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("benchmark outputs must be finite")


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    candidate: RuntimeCandidate
    observations: tuple[BenchmarkObservation, ...]
    stable: bool
    peak_memory_bytes: int

    def __post_init__(self) -> None:
        workloads = tuple(observation.workload for observation in self.observations)
        if len(workloads) != len(set(workloads)):
            raise ValueError("candidate evaluation workloads must be unique")
        if set(workloads) != set(self.candidate.workloads):
            raise ValueError("candidate evaluation must cover every supported workload")
        if self.peak_memory_bytes < 0:
            raise ValueError("candidate peak memory must not be negative")

    def observation(self, workload: Workload) -> BenchmarkObservation:
        for observation in self.observations:
            if observation.workload is workload:
                return observation
        raise KeyError(workload)


class CandidateEvaluator(Protocol):
    async def warmup(self, candidate: RuntimeCandidate) -> None: ...

    async def evaluate(self, candidate: RuntimeCandidate) -> CandidateEvaluation: ...


@dataclass(frozen=True, slots=True)
class RuntimeSelection:
    hardware_fingerprint: str
    selected: tuple[tuple[Workload, str], ...]
    rankings: tuple[tuple[Workload, tuple[str, ...]], ...]
    rejections: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "selected",
            tuple(sorted(self.selected, key=lambda item: item[0].value)),
        )
        object.__setattr__(
            self,
            "rankings",
            tuple(sorted(self.rankings, key=lambda item: item[0].value)),
        )
        object.__setattr__(self, "rejections", tuple(sorted(self.rejections)))
        if not self.hardware_fingerprint.strip():
            raise ValueError("hardware fingerprint must not be empty")
        selected_workloads = [workload for workload, _ in self.selected]
        ranking_workloads = [workload for workload, _ in self.rankings]
        if len(selected_workloads) != len(set(selected_workloads)):
            raise ValueError("selected workloads must be unique")
        if len(ranking_workloads) != len(set(ranking_workloads)):
            raise ValueError("ranking workloads must be unique")
        for workload, candidate in self.selected:
            if not candidate.strip() or candidate not in self.ranked(workload):
                raise ValueError("selected candidate must be present in workload ranking")
        if any(not names for _, names in self.rankings):
            raise ValueError("runtime rankings must not be empty")

    def selected_candidate(self, workload: Workload) -> str:
        for item_workload, candidate in self.selected:
            if item_workload is workload:
                return candidate
        raise KeyError(workload)

    def ranked(self, workload: Workload) -> tuple[str, ...]:
        for item_workload, candidates in self.rankings:
            if item_workload is workload:
                return candidates
        return ()

    def to_json(self) -> str:
        payload = {
            "hardware_fingerprint": self.hardware_fingerprint,
            "rankings": {
                workload.value: list(candidates) for workload, candidates in self.rankings
            },
            "rejections": [list(item) for item in self.rejections],
            "selected": {
                workload.value: candidate for workload, candidate in self.selected
            },
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> "RuntimeSelection":
        loaded: object = json.loads(value)
        if not isinstance(loaded, dict):
            raise ValueError("runtime selection cache must contain an object")
        raw = cast(dict[str, object], loaded)
        expected_keys = {"hardware_fingerprint", "rankings", "rejections", "selected"}
        if set(raw) != expected_keys:
            raise ValueError("runtime selection cache has unexpected fields")
        fingerprint = raw["hardware_fingerprint"]
        rankings = raw["rankings"]
        rejections = raw["rejections"]
        selected = raw["selected"]
        if (
            not isinstance(fingerprint, str)
            or not isinstance(rankings, dict)
            or not isinstance(selected, dict)
            or not isinstance(rejections, list)
        ):
            raise ValueError("runtime selection cache has invalid field types")
        typed_rankings = cast(dict[str, object], rankings)
        typed_selected = cast(dict[str, object], selected)
        typed_rejections = cast(list[object], rejections)
        return cls(
            hardware_fingerprint=fingerprint,
            selected=tuple(
                sorted(
                    (
                        (Workload(workload), _required_string(candidate))
                        for workload, candidate in typed_selected.items()
                    ),
                    key=lambda item: item[0].value,
                )
            ),
            rankings=tuple(
                sorted(
                    (
                        (
                            Workload(workload),
                            tuple(_required_string(name) for name in _required_list(names)),
                        )
                        for workload, names in typed_rankings.items()
                    ),
                    key=lambda item: item[0].value,
                )
            ),
            rejections=tuple(
                (
                    _required_string(_required_list(item)[0]),
                    _required_string(_required_list(item)[1]),
                )
                for item in typed_rejections
                if len(_required_list(item)) == 2
            ),
        )


class RuntimeSelectionCache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().absolute()
        if self.root.exists() and self.root.is_symlink():
            raise ValueError("runtime selection cache root must not be a symbolic link")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    def path_for(self, hardware_fingerprint: str) -> Path:
        if not hardware_fingerprint.strip() or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in hardware_fingerprint
        ):
            raise ValueError("hardware fingerprint contains unsafe path characters")
        return self.root / f"{hardware_fingerprint}.json"

    def load(self, hardware_fingerprint: str) -> RuntimeSelection | None:
        path = self.path_for(hardware_fingerprint)
        try:
            if not path.is_file() or path.is_symlink():
                return None
            selection = RuntimeSelection.from_json(path.read_text(encoding="utf-8"))
            if selection.hardware_fingerprint != hardware_fingerprint:
                return None
            return selection
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def save(self, selection: RuntimeSelection) -> Path:
        target = self.path_for(selection.hardware_fingerprint)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.stem}-",
            suffix=".tmp",
            dir=self.root,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
                stream.write(selection.to_json())
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, target)
            target.chmod(0o600)
            return target
        finally:
            if temporary.exists():
                temporary.unlink()


class HardwareRuntimeSelector:
    def __init__(
        self,
        policy: BenchmarkPolicy,
        evaluator: CandidateEvaluator,
        *,
        cache: RuntimeSelectionCache | None = None,
        progress: Callable[[RagProgressEvent], Awaitable[None]] | None = None,
        operation_id_factory: Callable[[], OperationId] | None = None,
    ) -> None:
        self.policy = policy
        self.evaluator = evaluator
        self.cache = cache
        self._progress = progress
        self._operation_id_factory = operation_id_factory

    async def select(
        self,
        hardware_fingerprint: str,
        candidates: tuple[RuntimeCandidate, ...],
    ) -> RuntimeSelection:
        if not candidates:
            await self._publish_failure(self._new_operation_id())
            raise ValueError("at least one local runtime candidate is required")
        cached = self.cache.load(hardware_fingerprint) if self.cache is not None else None
        candidate_names = {candidate.name for candidate in candidates}
        if cached is not None and self._cache_matches(cached, candidate_names):
            return cached

        operation_id = self._new_operation_id()
        await self._publish(
            RagProgressEvent(
                operation_id=operation_id,
                operation=RagOperation.BENCHMARK,
                phase=RagPhase.BENCHMARKING,
                state=RagProgressState.RUNNING,
                fallback_text="正在测试本机 RAG 加速方案…",
            )
        )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.policy.total_seconds
        evaluations: list[CandidateEvaluation] = []
        rejections: list[tuple[str, str]] = []
        for candidate in candidates:
            remaining = deadline - loop.time()
            if remaining <= 0:
                rejections.append((candidate.name, "total_budget_exhausted"))
                continue
            timeout = min(self.policy.candidate_seconds, remaining)
            try:
                evaluation = await asyncio.wait_for(
                    self._warm_and_evaluate(candidate),
                    timeout=timeout,
                )
            except TimeoutError:
                reason = (
                    "total_budget_exhausted"
                    if deadline - loop.time() <= 0 and timeout < self.policy.candidate_seconds
                    else "candidate_timeout"
                )
                rejections.append((candidate.name, reason))
                continue
            except Exception:
                rejections.append((candidate.name, "candidate_failure"))
                continue
            evaluations.append(evaluation)

        if not evaluations:
            await self._publish_failure(operation_id)
            raise RuntimeError("no local runtime candidate completed the benchmark")
        reference = self._reference(evaluations)
        accepted: list[CandidateEvaluation] = []
        for evaluation in evaluations:
            reason = self._rejection_reason(reference, evaluation)
            if reason is None:
                accepted.append(evaluation)
            else:
                rejections.append((evaluation.candidate.name, reason))
        if not accepted:
            await self._publish_failure(operation_id)
            raise RuntimeError("no local runtime candidate passed correctness gates")

        rankings: list[tuple[Workload, tuple[str, ...]]] = []
        selected: list[tuple[Workload, str]] = []
        for workload in Workload:
            ranked = tuple(
                evaluation.candidate.name
                for evaluation in sorted(
                    (
                        item
                        for item in accepted
                        if workload in item.candidate.workloads
                    ),
                    key=lambda item: (
                        item.observation(workload).latency_seconds,
                        item.candidate.name,
                    ),
                )
            )
            if ranked:
                rankings.append((workload, ranked))
                selected.append((workload, ranked[0]))
        result = RuntimeSelection(
            hardware_fingerprint=hardware_fingerprint,
            selected=tuple(sorted(selected, key=lambda item: item[0].value)),
            rankings=tuple(sorted(rankings, key=lambda item: item[0].value)),
            rejections=tuple(rejections),
        )
        if self.cache is not None:
            self.cache.save(result)
        selected_names = ", ".join(candidate for _, candidate in result.selected)
        await self._publish(
            RagProgressEvent(
                operation_id=operation_id,
                operation=RagOperation.BENCHMARK,
                phase=RagPhase.SELECTED,
                state=RagProgressState.RUNNING,
                fallback_text=f"已选择本地 RAG 运行方案：{selected_names}。",
            )
        )
        await self._publish(
            RagProgressEvent(
                operation_id=operation_id,
                operation=RagOperation.BENCHMARK,
                phase=RagPhase.COMPLETED,
                state=RagProgressState.COMPLETED,
                fallback_text="本机 RAG 加速方案准备完成。",
            )
        )
        return result

    def _new_operation_id(self) -> OperationId:
        if self._operation_id_factory is not None:
            return self._operation_id_factory()
        import secrets

        return OperationId(secrets.token_hex(16))

    async def _publish(self, event: RagProgressEvent) -> None:
        if self._progress is None:
            return
        try:
            await self._progress(event)
        except Exception:
            pass

    async def _publish_failure(self, operation_id: OperationId) -> None:
        await self._publish(
            RagProgressEvent(
                operation_id=operation_id,
                operation=RagOperation.BENCHMARK,
                phase=RagPhase.FAILED,
                state=RagProgressState.FAILED,
                error_code=RagErrorCode.MODEL_INITIALIZATION_FAILED,
                fallback_text="本机 RAG 加速方案测试失败，将保持安全的本地运行策略。",
            )
        )

    async def _warm_and_evaluate(
        self,
        candidate: RuntimeCandidate,
    ) -> CandidateEvaluation:
        await self.evaluator.warmup(candidate)
        evaluation = await self.evaluator.evaluate(candidate)
        if evaluation.candidate != candidate:
            raise ValueError("candidate evaluator returned a mismatched candidate")
        return evaluation

    @staticmethod
    def _reference(evaluations: Sequence[CandidateEvaluation]) -> CandidateEvaluation:
        for evaluation in evaluations:
            if evaluation.candidate.provider == "CPUExecutionProvider":
                return evaluation
        raise RuntimeError("CPU reference candidate did not complete the benchmark")

    def _rejection_reason(
        self,
        reference: CandidateEvaluation,
        candidate: CandidateEvaluation,
    ) -> str | None:
        if not candidate.stable:
            return "stability_gate"
        if (
            self.policy.max_peak_memory_bytes is not None
            and candidate.peak_memory_bytes > self.policy.max_peak_memory_bytes
        ):
            return "memory_gate"
        for workload in (Workload.QUERY_EMBEDDING, Workload.BATCH_EMBEDDING):
            reference_outputs = reference.observation(workload).embedding_outputs
            candidate_outputs = candidate.observation(workload).embedding_outputs
            if len(reference_outputs) != len(candidate_outputs):
                return "embedding_shape"
            if any(
                _cosine(left, right) < self.policy.embedding_cosine_tolerance
                for left, right in zip(reference_outputs, candidate_outputs, strict=True)
            ):
                return "embedding_cosine"
        reference_scores = reference.observation(Workload.RERANKER).reranker_scores
        candidate_scores = candidate.observation(Workload.RERANKER).reranker_scores
        if len(reference_scores) != len(candidate_scores):
            return "reranker_shape"
        if _ranking(reference_scores) != _ranking(candidate_scores):
            return "reranker_ranking"
        if any(
            abs(left - right) > self.policy.reranker_score_tolerance
            for left, right in zip(reference_scores, candidate_scores, strict=True)
        ):
            return "reranker_score"
        return None

    @staticmethod
    def _cache_matches(selection: RuntimeSelection, candidate_names: set[str]) -> bool:
        ranked_names = {
            candidate for _, ranking in selection.rankings for candidate in ranking
        }
        return bool(ranked_names) and ranked_names.issubset(candidate_names)


@dataclass(frozen=True, slots=True)
class RuntimeFallbackEvent:
    workload: Workload
    failed_candidate: str
    fallback_candidate: str
    reason: str


class SafeRuntimeRouter:
    def __init__(
        self,
        selection: RuntimeSelection,
        *,
        event_publisher: Callable[[RuntimeFallbackEvent], None] | None = None,
        rag_progress_publisher: Callable[[RagProgressEvent], None] | None = None,
        operation_id_factory: Callable[[], OperationId] | None = None,
    ) -> None:
        self.selection = selection
        self._event_publisher = event_publisher
        self._rag_progress_publisher = rag_progress_publisher
        self._operation_id_factory = operation_id_factory
        self._blacklisted: set[str] = set()
        self._emitted: set[tuple[Workload, str]] = set()

    @property
    def blacklisted(self) -> tuple[str, ...]:
        return tuple(sorted(self._blacklisted))

    async def execute(
        self,
        workload: Workload,
        operation: Callable[[str], Awaitable[T]],
    ) -> T:
        candidates = tuple(
            candidate
            for candidate in self.selection.ranked(workload)
            if candidate not in self._blacklisted
        )
        if not candidates:
            raise RuntimeError("no verified local runtime candidate is available")
        last_error: Exception | None = None
        for index, candidate in enumerate(candidates):
            try:
                return await operation(candidate)
            except Exception as exc:
                last_error = exc
                self._blacklisted.add(candidate)
                fallback = next(
                    (
                        item
                        for item in candidates[index + 1 :]
                        if item not in self._blacklisted
                    ),
                    "",
                )
                if fallback:
                    self._publish_once(
                        RuntimeFallbackEvent(
                            workload=workload,
                            failed_candidate=candidate,
                            fallback_candidate=fallback,
                            reason="runtime_failure",
                        )
                    )
        raise RuntimeError("all verified local runtime candidates failed") from last_error

    def _publish_once(self, event: RuntimeFallbackEvent) -> None:
        key = (event.workload, event.failed_candidate)
        if key in self._emitted:
            return
        self._emitted.add(key)
        if self._event_publisher is None:
            self._publish_rag_progress(event)
        else:
            try:
                self._event_publisher(event)
            except Exception:
                pass
            self._publish_rag_progress(event)

    def _publish_rag_progress(self, event: RuntimeFallbackEvent) -> None:
        if self._rag_progress_publisher is None:
            return
        try:
            import secrets

            operation_id = (
                self._operation_id_factory()
                if self._operation_id_factory is not None
                else OperationId(secrets.token_hex(16))
            )
            self._rag_progress_publisher(
                runtime_fallback_progress_event(operation_id, event)
            )
        except Exception:
            # Progress delivery is best-effort and cannot alter inference state.
            pass


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0 or right_norm <= 0:
        return -1.0
    return dot / (left_norm * right_norm)


def _ranking(scores: Sequence[float]) -> tuple[int, ...]:
    return tuple(sorted(range(len(scores)), key=lambda index: (-scores[index], index)))


def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("runtime selection cache requires non-empty strings")
    return value


def _required_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("runtime selection cache requires arrays")
    return cast(list[object], value)


__all__ = [
    "BenchmarkObservation",
    "BenchmarkPolicy",
    "CandidateEvaluation",
    "CandidateEvaluator",
    "HardwareRuntimeSelector",
    "RuntimeFallbackEvent",
    "RuntimeSelection",
    "RuntimeSelectionCache",
    "SafeRuntimeRouter",
]
