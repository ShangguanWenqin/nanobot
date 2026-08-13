from __future__ import annotations

from nanobot.rag.smoke import ProviderBenchmark, candidate_providers, select_fastest_profiles


def _benchmark(
    provider: str,
    *,
    query: float,
    batch: float,
    reranker: float,
) -> ProviderBenchmark:
    return ProviderBenchmark(
        provider=provider,
        embedding_load_seconds=1.0,
        reranker_load_seconds=1.0,
        query_embedding_seconds=query,
        batch_embedding_seconds=batch,
        reranker_seconds=reranker,
        query_vector=(1.0, 0.0),
        passage_vectors=((1.0, 0.0), (0.0, 1.0)),
        reranker_scores=(0.9, 0.1),
    )


def test_candidate_providers_keep_cpu_baseline_and_only_compatible_accelerators() -> None:
    available = (
        "AzureExecutionProvider",
        "CoreMLExecutionProvider",
        "CUDAExecutionProvider",
        "CPUExecutionProvider",
    )

    assert candidate_providers(available, os_name="Darwin", architecture="arm64") == (
        "CPUExecutionProvider",
        "CoreMLExecutionProvider",
    )
    assert candidate_providers(available, os_name="Linux", architecture="x86_64") == (
        "CPUExecutionProvider",
        "CUDAExecutionProvider",
    )


def test_profile_selection_rejects_incorrect_accelerator_and_selects_per_workload() -> None:
    cpu = _benchmark("CPUExecutionProvider", query=0.3, batch=0.2, reranker=0.4)
    accelerator = _benchmark("CoreMLExecutionProvider", query=0.1, batch=0.5, reranker=0.2)
    incorrect = _benchmark("BrokenExecutionProvider", query=0.01, batch=0.01, reranker=0.01)
    incorrect = ProviderBenchmark(
        **{
            **incorrect.as_dict(),
            "query_vector": (0.0, 1.0),
            "passage_vectors": ((0.0, 1.0), (1.0, 0.0)),
        }
    )

    result = select_fastest_profiles((cpu, accelerator, incorrect))

    assert result.selected == {
        "query_embedding": "CoreMLExecutionProvider",
        "batch_embedding": "CPUExecutionProvider",
        "reranker": "CoreMLExecutionProvider",
    }
    assert result.rejections == {"BrokenExecutionProvider": "correctness_gate_failed"}
