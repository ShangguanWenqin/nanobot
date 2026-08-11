"""Configuration models for the optional local private RAG subsystem."""

from __future__ import annotations

from collections.abc import Collection
from typing import Literal

from pydantic import Field, model_validator

from nanobot.config_base import Base

_MIB = 1024**2
_GIB = 1024**3


class RagStorageConfig(Base):
    """Private-store quotas and host disk safety limits."""

    root: str = "~/.nanobot/rag"
    per_user_quota_bytes: int = Field(default=_GIB, ge=1)
    global_max_bytes: int = Field(default=100 * _GIB, ge=1)
    min_free_disk_bytes: int = Field(default=2 * _GIB, ge=0)


class RagParsingConfig(Base):
    """Fail-closed document parser bounds."""

    max_file_bytes: int = Field(default=50 * _MIB, ge=1)
    max_attachments_per_batch: int = Field(default=10, ge=1, le=100)
    max_pdf_pages: int = Field(default=100, ge=1)
    max_pdf_content_stream_bytes: int = Field(default=32 * _MIB, ge=1)
    max_extracted_chars: int = Field(default=200_000, ge=1)
    max_archive_members: int = Field(default=1_000, ge=1)
    max_archive_uncompressed_bytes: int = Field(default=200 * _MIB, ge=1)
    max_archive_member_bytes: int = Field(default=64 * _MIB, ge=1)
    max_table_rows: int = Field(default=100_000, ge=1)
    max_table_cells: int = Field(default=500_000, ge=1)
    max_spreadsheet_sheets: int = Field(default=200, ge=1)
    max_presentation_slides: int = Field(default=500, ge=1)
    max_structure_depth: int = Field(default=16, ge=1)
    timeout_seconds: float = Field(default=60.0, gt=0)


class RagChunkingConfig(Base):
    """Structure-aware chunk sizing expressed in embedding-model tokens."""

    target_tokens: int = Field(default=350, ge=1)
    overlap_tokens: int = Field(default=50, ge=0)
    max_sequence_tokens: int = Field(default=512, ge=1)
    version: str = Field(default="rag-chunker-v1", min_length=1)

    @model_validator(mode="after")
    def validate_token_bounds(self) -> "RagChunkingConfig":
        if self.overlap_tokens >= self.target_tokens:
            raise ValueError("overlap_tokens must be smaller than target_tokens")
        if self.target_tokens > self.max_sequence_tokens:
            raise ValueError("target_tokens must not exceed max_sequence_tokens")
        return self


class RagModelsConfig(Base):
    """Names and persisted-vector compatibility settings for local models."""

    embedding_profile: str = Field(default="multilingual-e5-small-v1", min_length=1)
    reranker_profile: str = Field(default="bge-reranker-base-v1", min_length=1)
    embedding_dimension: int = Field(default=384, ge=1)
    vector_index_dimension: int = Field(default=384, ge=1)
    cache_root: str = "~/.nanobot/models/rag"
    auto_download: bool = True

    @model_validator(mode="after")
    def validate_vector_compatibility(self) -> "RagModelsConfig":
        if self.vector_index_dimension != self.embedding_dimension:
            raise ValueError(
                "vector_index_dimension must match embedding_dimension for the active profile"
            )
        return self


RagRuntimeMode = Literal["auto", "cpu", "coreml", "cuda", "openvino", "directml"]


class RagRuntimeConfig(Base):
    """Local execution-provider selection and correctness-gate settings."""

    mode: RagRuntimeMode = "auto"
    benchmark_total_seconds: float = Field(default=60.0, gt=0)
    benchmark_candidate_seconds: float = Field(default=10.0, gt=0)
    embedding_cosine_tolerance: float = Field(default=0.999, ge=-1.0, le=1.0)
    reranker_score_tolerance: float = Field(default=0.001, ge=0)
    cpu_threads: int = Field(default=0, ge=0)
    max_parallel_inference: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_benchmark_budget(self) -> "RagRuntimeConfig":
        if self.benchmark_candidate_seconds > self.benchmark_total_seconds:
            raise ValueError(
                "benchmark_candidate_seconds must not exceed benchmark_total_seconds"
            )
        return self

    def validate_available_providers(self, available: Collection[str]) -> None:
        """Reject a forced provider that is absent from the local runtime.

        This explicit runtime validation keeps importing the configuration
        schema independent from the optional ``onnxruntime`` package.
        """

        provider_by_mode: dict[RagRuntimeMode, str] = {
            "auto": "CPUExecutionProvider",
            "cpu": "CPUExecutionProvider",
            "coreml": "CoreMLExecutionProvider",
            "cuda": "CUDAExecutionProvider",
            "openvino": "OpenVINOExecutionProvider",
            "directml": "DmlExecutionProvider",
        }
        required = provider_by_mode[self.mode]
        if required not in available:
            raise ValueError(
                f"RAG runtime mode {self.mode!r} requires unavailable provider {required}"
            )


class RagRetrievalConfig(Base):
    """Hybrid retrieval, fusion, reranking, and evidence policy."""

    lexical_candidates: int = Field(default=40, ge=1)
    dense_candidates: int = Field(default=40, ge=1)
    rerank_candidates: int = Field(default=30, ge=1)
    max_evidence: int = Field(default=6, ge=1)
    rrf_k: int = Field(default=60, ge=1)
    allow_lexical_degraded_mode: bool = True
    acceptance_threshold_override: float | None = None

    @model_validator(mode="after")
    def validate_candidate_counts(self) -> "RagRetrievalConfig":
        if self.rerank_candidates > self.lexical_candidates + self.dense_candidates:
            raise ValueError(
                "rerank_candidates must not exceed all lexical and dense candidates"
            )
        if self.max_evidence > self.rerank_candidates:
            raise ValueError("max_evidence must not exceed rerank_candidates")
        return self


class RagIngestionConfig(Base):
    """Persistent ingestion scheduling and retry policy."""

    concurrency: int = Field(default=1, ge=1)
    embedding_batch_size: int = Field(default=32, ge=1)
    max_transient_retries: int = Field(default=2, ge=0)
    job_retention_days: int = Field(default=30, ge=1)


class RagProgressConfig(Base):
    """Progress publication deadlines and throttling."""

    query_start_deadline_seconds: float = Field(default=0.5, gt=0)
    min_update_interval_seconds: float = Field(default=1.0, ge=0)


class RagConfig(Base):
    """Root configuration for local private RAG."""

    enabled: bool = False
    storage: RagStorageConfig = Field(default_factory=RagStorageConfig)
    parsing: RagParsingConfig = Field(default_factory=RagParsingConfig)
    chunking: RagChunkingConfig = Field(default_factory=RagChunkingConfig)
    models: RagModelsConfig = Field(default_factory=RagModelsConfig)
    runtime: RagRuntimeConfig = Field(default_factory=RagRuntimeConfig)
    retrieval: RagRetrievalConfig = Field(default_factory=RagRetrievalConfig)
    ingestion: RagIngestionConfig = Field(default_factory=RagIngestionConfig)
    progress: RagProgressConfig = Field(default_factory=RagProgressConfig)

    @model_validator(mode="after")
    def validate_storage_relationships(self) -> "RagConfig":
        if self.storage.global_max_bytes < self.storage.per_user_quota_bytes:
            raise ValueError(
                "global_max_bytes must be at least per_user_quota_bytes"
            )
        if self.parsing.max_file_bytes > self.storage.per_user_quota_bytes:
            raise ValueError("max_file_bytes must not exceed per_user_quota_bytes")
        return self


__all__ = [
    "RagChunkingConfig",
    "RagConfig",
    "RagIngestionConfig",
    "RagModelsConfig",
    "RagParsingConfig",
    "RagProgressConfig",
    "RagRetrievalConfig",
    "RagRuntimeConfig",
    "RagRuntimeMode",
    "RagStorageConfig",
]
