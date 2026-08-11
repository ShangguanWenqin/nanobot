from __future__ import annotations

import pytest
from pydantic import ValidationError

from nanobot.config.schema import Config
from nanobot.rag.config import RagConfig, RagRuntimeConfig

GIB = 1024**3
MIB = 1024**2


def test_rag_defaults_are_disabled_local_and_bounded() -> None:
    config = Config()

    assert config.rag.enabled is False
    assert config.rag.storage.per_user_quota_bytes == GIB
    assert config.rag.parsing.max_file_bytes == 50 * MIB
    assert config.rag.models.embedding_profile == "multilingual-e5-small-v1"
    assert config.rag.models.reranker_profile == "bge-reranker-base-v1"
    assert config.rag.runtime.mode == "auto"
    assert config.rag.retrieval.lexical_candidates == 40
    assert config.rag.retrieval.dense_candidates == 40
    assert config.rag.retrieval.rerank_candidates == 30
    assert config.rag.retrieval.max_evidence == 6


def test_rag_config_accepts_camel_case_and_serializes_it() -> None:
    config = Config.model_validate(
        {
            "rag": {
                "enabled": True,
                "storage": {"perUserQuotaBytes": 2 * GIB},
                "runtime": {"benchmarkTotalSeconds": 30},
            }
        }
    )

    assert config.rag.storage.per_user_quota_bytes == 2 * GIB
    assert config.rag.runtime.benchmark_total_seconds == 30
    dumped = config.model_dump(by_alias=True)
    assert dumped["rag"]["storage"]["perUserQuotaBytes"] == 2 * GIB


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "storage": {
                    "per_user_quota_bytes": GIB,
                    "global_max_bytes": GIB - 1,
                }
            },
            "global_max_bytes",
        ),
        (
            {
                "storage": {"per_user_quota_bytes": 10 * MIB},
                "parsing": {"max_file_bytes": 11 * MIB},
            },
            "max_file_bytes",
        ),
        (
            {"chunking": {"target_tokens": 300, "overlap_tokens": 300}},
            "overlap_tokens",
        ),
        (
            {
                "models": {
                    "embedding_dimension": 384,
                    "vector_index_dimension": 768,
                }
            },
            "vector_index_dimension",
        ),
        (
            {
                "retrieval": {
                    "rerank_candidates": 5,
                    "max_evidence": 6,
                }
            },
            "max_evidence",
        ),
    ],
)
def test_rag_cross_field_validation_rejects_incompatible_values(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        RagConfig.model_validate(overrides)


def test_forced_runtime_requires_an_installed_execution_provider() -> None:
    runtime = RagRuntimeConfig(mode="cuda")

    with pytest.raises(ValueError, match="CUDAExecutionProvider"):
        runtime.validate_available_providers({"CPUExecutionProvider"})

    runtime.validate_available_providers(
        {"CPUExecutionProvider", "CUDAExecutionProvider"}
    )


def test_auto_runtime_only_requires_the_cpu_baseline() -> None:
    runtime = RagRuntimeConfig(mode="auto")

    runtime.validate_available_providers({"CPUExecutionProvider"})
    with pytest.raises(ValueError, match="CPUExecutionProvider"):
        runtime.validate_available_providers(set())
