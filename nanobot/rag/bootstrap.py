"""Production assembly for the optional local private RAG application."""

from __future__ import annotations

import asyncio
import platform
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from nanobot.bus.queue import MessageBus
from nanobot.rag.application import PrincipalRagServices, ServiceBackedRagApplication
from nanobot.rag.builtin_models import BGE_RERANKER_BASE, MULTILINGUAL_E5_SMALL
from nanobot.rag.chunking import DeterministicChunker, EmbeddingInputBuilder, TokenCodec
from nanobot.rag.config import RagConfig
from nanobot.rag.deletion import RagDeletionService
from nanobot.rag.inference_scheduler import PriorityInferenceScheduler
from nanobot.rag.ingestion import RagIngestionService
from nanobot.rag.lexical import BilingualLexicalAnalyzer, LexicalRepository
from nanobot.rag.library_status import LibraryStatusService, RuntimeProfileReport
from nanobot.rag.local_inference import LocalEmbedder, LocalReranker, LocalTokenizer
from nanobot.rag.manager import RagManager
from nanobot.rag.model_cache import HuggingFaceDownloader, ModelCache
from nanobot.rag.parser_worker import parse_document_isolated
from nanobot.rag.progress import build_bus_rag_progress_delivery
from nanobot.rag.protocols import DiskProbe, Embedder, Reranker
from nanobot.rag.quota import QuotaManager
from nanobot.rag.retrieval import HybridRetriever, LexicalSearch, SqliteCandidateLoader
from nanobot.rag.smoke import (
    ProviderBenchmark,
    benchmark_provider,
    candidate_providers,
    select_fastest_profiles,
)
from nanobot.rag.store import RagStore
from nanobot.rag.types import PrincipalId
from nanobot.rag.vector_store import VectorGenerationRepository


@dataclass(frozen=True, slots=True)
class LocalRagRuntime:
    query_embedder: Embedder
    batch_embedder: Embedder
    reranker: Reranker
    token_codec: TokenCodec
    profiles: RuntimeProfileReport


class FilesystemDiskProbe(DiskProbe):
    """Read host capacity without following links outside the managed RAG root."""

    def free_bytes(self, path: Path) -> int:
        path.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(path).free

    def used_bytes(self, path: Path) -> int:
        if not path.exists():
            return 0
        total = 0
        for item in path.rglob("*"):
            if item.is_symlink() or not item.is_file():
                continue
            total += item.stat().st_size
        return total


class BackgroundTaskTracker:
    """Own fire-and-forget ingestion/deletion work so gateway shutdown can await it."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def schedule(self, awaitable: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(awaitable, name="nanobot-rag-job")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task


_PROVIDER_BY_MODE = {
    "cpu": "CPUExecutionProvider",
    "coreml": "CoreMLExecutionProvider",
    "cuda": "CUDAExecutionProvider",
    "openvino": "OpenVINOExecutionProvider",
    "directml": "DmlExecutionProvider",
}


async def prepare_local_rag_runtime(config: RagConfig) -> LocalRagRuntime:
    """Prepare immutable models and select the fastest correct installed provider."""

    if config.models.embedding_profile != "multilingual-e5-small-v1":
        raise ValueError("configured RAG embedding profile is not built in")
    if config.models.reranker_profile != "bge-reranker-base-v1":
        raise ValueError("configured RAG reranker profile is not built in")
    if config.models.embedding_dimension != MULTILINGUAL_E5_SMALL.dimension:
        raise ValueError("configured RAG embedding dimension does not match the built-in model")
    cache = ModelCache(config.models.cache_root)
    downloader = HuggingFaceDownloader()
    offline = not config.models.auto_download
    embedding_dir = await asyncio.to_thread(
        cache.prepare, MULTILINGUAL_E5_SMALL, downloader, offline=offline
    )
    reranker_dir = await asyncio.to_thread(
        cache.prepare, BGE_RERANKER_BASE, downloader, offline=offline
    )
    ort: Any = import_module("onnxruntime")
    compatible = candidate_providers(
        tuple(ort.get_available_providers()),
        os_name=platform.system(),
        architecture=platform.machine(),
    )
    if config.runtime.mode == "auto":
        loop = asyncio.get_running_loop()
        deadline = loop.time() + config.runtime.benchmark_total_seconds
        benchmarks: list[ProviderBenchmark] = []
        for provider in compatible:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            timeout = min(config.runtime.benchmark_candidate_seconds, remaining)
            try:
                benchmark = await asyncio.wait_for(
                    benchmark_provider(provider, embedding_dir, reranker_dir),
                    timeout=timeout,
                )
            except Exception:
                if provider == "CPUExecutionProvider":
                    raise RuntimeError("CPU RAG runtime benchmark failed") from None
                continue
            benchmarks.append(benchmark)
        if not any(item.provider == "CPUExecutionProvider" for item in benchmarks):
            raise RuntimeError("CPU RAG runtime benchmark did not complete")
        selection = select_fastest_profiles(
            tuple(benchmarks),
            embedding_cosine_tolerance=config.runtime.embedding_cosine_tolerance,
            reranker_score_tolerance=config.runtime.reranker_score_tolerance,
        ).selected
    else:
        forced = _PROVIDER_BY_MODE[config.runtime.mode]
        if forced not in compatible:
            raise ValueError(
                f"forced RAG runtime mode {config.runtime.mode!r} is unavailable on this host"
            )
        selection = {
            "query_embedding": forced,
            "batch_embedding": forced,
            "reranker": forced,
        }

    query_provider = selection["query_embedding"]
    batch_provider = selection["batch_embedding"]
    reranker_provider = selection["reranker"]
    query_embedder = LocalEmbedder(
        MULTILINGUAL_E5_SMALL,
        embedding_dir,
        max_concurrency=config.runtime.max_parallel_inference,
        execution_provider=query_provider,
    )
    batch_embedder = LocalEmbedder(
        MULTILINGUAL_E5_SMALL,
        embedding_dir,
        batch_size=config.ingestion.embedding_batch_size,
        max_concurrency=config.runtime.max_parallel_inference,
        execution_provider=batch_provider,
    )
    reranker = LocalReranker(
        BGE_RERANKER_BASE,
        reranker_dir,
        max_concurrency=config.runtime.max_parallel_inference,
        execution_provider=reranker_provider,
    )
    await query_embedder.validate_samples()
    if batch_provider != query_provider:
        await batch_embedder.validate_samples()
    await reranker.validate_samples()
    token_codec = LocalTokenizer(
        embedding_dir / MULTILINGUAL_E5_SMALL.tokenizer_path,
        max_length=MULTILINGUAL_E5_SMALL.max_sequence_tokens,
    )

    def label(provider: str) -> str:
        return provider.removesuffix("ExecutionProvider").casefold() + "-float32"

    return LocalRagRuntime(
        query_embedder=query_embedder,
        batch_embedder=batch_embedder,
        reranker=reranker,
        token_codec=token_codec,
        profiles=RuntimeProfileReport(
            query_embedding=label(query_provider),
            batch_embedding=label(batch_provider),
            reranker=label(reranker_provider),
            embedding_profile_id=str(MULTILINGUAL_E5_SMALL.profile_id),
            reranker_profile_id=str(BGE_RERANKER_BASE.profile_id),
        ),
    )


class PrincipalServiceFactory:
    def __init__(self, config: RagConfig, runtime: LocalRagRuntime) -> None:
        self.config = config
        self.runtime = runtime
        self.scheduler = PriorityInferenceScheduler()
        self._services: dict[PrincipalId, PrincipalRagServices] = {}
        self._disk_probe = FilesystemDiskProbe()

    def __call__(self, principal_id: PrincipalId) -> PrincipalRagServices:
        existing = self._services.get(principal_id)
        if existing is not None:
            return existing
        store = RagStore.open(self.config.storage.root, principal_id)
        quota = QuotaManager(
            store,
            per_user_quota_bytes=self.config.storage.per_user_quota_bytes,
            global_max_bytes=self.config.storage.global_max_bytes,
            min_free_disk_bytes=self.config.storage.min_free_disk_bytes,
            disk_probe=self._disk_probe,
        )
        vectors = VectorGenerationRepository(
            store, dimension=self.runtime.batch_embedder.dimension
        )
        vectors.reconcile_startup()
        lexical = LexicalRepository(store, BilingualLexicalAnalyzer())
        chunker = DeterministicChunker(self.config.chunking, self.runtime.token_codec)
        input_builder = EmbeddingInputBuilder(
            self.runtime.token_codec,
            max_sequence_tokens=self.config.chunking.max_sequence_tokens,
        )

        async def parse(path: Path):
            return await parse_document_isolated(path, self.config.parsing)

        def active_generation() -> str | None:
            with store.connect() as connection:
                row = connection.execute(
                    "SELECT active_generation_id FROM store_manifest WHERE singleton = 1"
                ).fetchone()
            return str(row[0]) if row is not None and row[0] is not None else None

        def common_ids() -> str:
            return uuid4().hex

        def clock() -> datetime:
            return datetime.now(UTC)
        ingestion = RagIngestionService(
            store=store,
            quota=quota,
            parser=parse,
            parsing_config=self.config.parsing,
            chunker=chunker,
            input_builder=input_builder,
            embedder=self.runtime.batch_embedder,
            vectors=vectors,
            lexical=lexical,
            clock=clock,
            id_factory=common_ids,
            inference_scheduler=self.scheduler,
            embedding_batch_size=self.config.ingestion.embedding_batch_size,
        )
        services = PrincipalRagServices(
            ingestion=ingestion,
            deletion=RagDeletionService(
                store=store,
                embedder=self.runtime.batch_embedder,
                input_builder=input_builder,
                vectors=vectors,
                lexical=lexical,
                clock=clock,
                id_factory=common_ids,
            ),
            status=LibraryStatusService(
                store,
                quota,
                runtime_profile=self.runtime.profiles,
                clock=clock,
                job_retention=timedelta(days=self.config.ingestion.job_retention_days),
            ),
            retrieval=HybridRetriever(
                config=self.config.retrieval,
                lexical=cast(LexicalSearch, lexical),
                vectors=vectors,
                embedder=self.runtime.query_embedder,
                reranker=self.runtime.reranker,
                input_builder=input_builder,
                candidate_loader=SqliteCandidateLoader(
                    store,
                    embedding_profile_id=str(self.runtime.query_embedder.profile_id),
                ),
                active_generation=active_generation,
                acceptance_threshold=(
                    self.config.retrieval.acceptance_threshold_override
                    if self.config.retrieval.acceptance_threshold_override is not None
                    else float(getattr(self.runtime.reranker, "acceptance_threshold", 0.5))
                ),
                inference_scheduler=self.scheduler,
            ),
        )
        self._services[principal_id] = services
        return services


def build_rag_application(
    config: RagConfig,
    bus: MessageBus,
    runtime: LocalRagRuntime,
) -> tuple[ServiceBackedRagApplication, RagManager]:
    """Bind shared local inference to lazily-created, isolated principal stores."""

    factory = PrincipalServiceFactory(config, runtime)
    tasks = BackgroundTaskTracker()
    application = ServiceBackedRagApplication(
        factory,
        schedule=tasks.schedule,
        progress=build_bus_rag_progress_delivery(bus),
        id_factory=lambda: uuid4().hex,
    )
    return application, RagManager(config, components=(factory.scheduler, tasks))


__all__ = [
    "BackgroundTaskTracker",
    "FilesystemDiskProbe",
    "LocalRagRuntime",
    "PrincipalServiceFactory",
    "build_rag_application",
    "prepare_local_rag_runtime",
]
