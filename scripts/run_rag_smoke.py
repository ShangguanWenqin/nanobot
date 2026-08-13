"""Run real local RAG model and execution-provider release smoke tests."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import tempfile
import time
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast

from nanobot.rag.builtin_models import BGE_RERANKER_BASE, MULTILINGUAL_E5_SMALL
from nanobot.rag.config import RagParsingConfig
from nanobot.rag.model_cache import ModelCache
from nanobot.rag.parser import parse_document
from nanobot.rag.smoke import (
    ProviderBenchmark,
    benchmark_provider,
    candidate_providers,
    select_fastest_profiles,
)

_QUERY = "nanobot 如何使用私人知识库？"
_PASSAGES = (
    "使用 /rag add 明确把附件加入当前用户的私人知识库。",
    "今天天气晴朗，适合户外活动。",
    "Use /rag ask to query the private knowledge base and receive cited evidence.",
)


class _OnnxRuntime(Protocol):
    __version__: str

    def get_available_providers(self) -> list[str]: ...


class _Index(Protocol):
    def add(self, keys: Any, vectors: Any) -> None: ...

    def save(self, path: str) -> bytes | None: ...

    def search(self, vector: Any, count: int) -> Any: ...


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--provider",
        action="append",
        help="ONNX Execution Provider；可重复指定。默认测试本机全部兼容候选。",
    )
    return parser.parse_args()


def _index_metrics(benchmark: ProviderBenchmark) -> dict[str, Any]:
    import numpy as np
    from usearch.index import Index

    with tempfile.TemporaryDirectory(prefix="nanobot-rag-smoke-") as temporary:
        path = Path(temporary) / "smoke.usearch"
        started = time.perf_counter()
        index = cast(
            _Index,
            Index(ndim=len(benchmark.query_vector), metric="cos", dtype="f32"),
        )
        index.add(
            np.arange(len(benchmark.passage_vectors), dtype=np.uint64),
            np.asarray(benchmark.passage_vectors, dtype=np.float32),
        )
        index.save(str(path))
        index_seconds = time.perf_counter() - started
        started = time.perf_counter()
        matches = index.search(
            np.asarray(benchmark.query_vector, dtype=np.float32),
            len(benchmark.passage_vectors),
        )
        retrieval_seconds = time.perf_counter() - started
        return {
            "build_seconds": index_seconds,
            "bytes": path.stat().st_size,
            "retrieval_seconds": retrieval_seconds,
            "keys": [int(key) for key in matches.keys],
            "distances": [float(distance) for distance in matches.distances],
        }


def _parse_metrics() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="nanobot-rag-parse-") as temporary:
        path = Path(temporary) / "smoke.md"
        path.write_text("\n\n".join(_PASSAGES), encoding="utf-8")
        started = time.perf_counter()
        parsed = parse_document(path, RagParsingConfig())
        return {
            "seconds": time.perf_counter() - started,
            "blocks": len(parsed.blocks),
            "characters": parsed.total_chars,
            "format": parsed.document_format.value,
        }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    ort = cast(_OnnxRuntime, import_module("onnxruntime"))

    cache = ModelCache(args.cache_dir)
    embedding_dir = cache.prepare(
        MULTILINGUAL_E5_SMALL,
        downloader=_downloader(),
        offline=args.offline,
    )
    reranker_dir = cache.prepare(
        BGE_RERANKER_BASE,
        downloader=_downloader(),
        offline=args.offline,
    )
    compatible = candidate_providers(
        tuple(ort.get_available_providers()),
        os_name=platform.system(),
        architecture=platform.machine(),
    )
    providers = tuple(args.provider) if args.provider else compatible
    if "CPUExecutionProvider" not in providers:
        raise ValueError("真实模型发布冒烟必须包含 CPUExecutionProvider 基线")
    unavailable = set(providers).difference(compatible)
    if unavailable:
        raise ValueError(f"本机没有兼容的 Execution Provider：{sorted(unavailable)}")
    benchmarks = tuple(
        [await benchmark_provider(provider, embedding_dir, reranker_dir) for provider in providers]
    )
    selection = select_fastest_profiles(benchmarks)
    cpu = next(item for item in benchmarks if item.provider == "CPUExecutionProvider")
    return {
        "schema_version": 1,
        "host": {
            "os": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "onnxruntime": ort.__version__,
            "available_providers": ort.get_available_providers(),
        },
        "models": {
            "embedding_profile": MULTILINGUAL_E5_SMALL.profile_id,
            "reranker_profile": BGE_RERANKER_BASE.profile_id,
        },
        "parsing": _parse_metrics(),
        "index": _index_metrics(cpu),
        "benchmarks": [
            {
                "provider": item.provider,
                "embedding_load_seconds": item.embedding_load_seconds,
                "reranker_load_seconds": item.reranker_load_seconds,
                "query_embedding_seconds": item.query_embedding_seconds,
                "batch_embedding_seconds": item.batch_embedding_seconds,
                "reranker_seconds": item.reranker_seconds,
                "dimension": len(item.query_vector),
                "query_passage_cosines": [
                    float(
                        sum(a * b for a, b in zip(item.query_vector, passage, strict=True))
                    )
                    for passage in item.passage_vectors
                ],
                "reranker_scores": item.reranker_scores,
            }
            for item in benchmarks
        ],
        "selected": selection.selected,
        "rejections": selection.rejections,
    }


def _downloader():
    from nanobot.rag.model_cache import HuggingFaceDownloader

    return HuggingFaceDownloader()


def main() -> None:
    args = _arguments()
    report = asyncio.run(_run(args))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
