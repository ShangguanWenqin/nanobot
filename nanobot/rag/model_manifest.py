"""Immutable local ONNX model manifest types."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

_SHA256 = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{40}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def canonical_output_sha256(values: Sequence[float], *, decimals: int = 6) -> str:
    """Hash numerically stable, platform-independent validation output text."""

    if decimals < 0 or decimals > 12:
        raise ValueError("validation decimals must be between zero and twelve")
    formatted: list[str] = []
    for value in values:
        if not math.isfinite(value):
            raise ValueError("validation output values must be finite")
        rounded = round(float(value), decimals)
        if rounded == 0:
            rounded = 0.0
        formatted.append(f"{rounded:.{decimals}f}")
    canonical = json.dumps(formatted, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


class ModelKind(StrEnum):
    EMBEDDING = "embedding"
    RERANKER = "reranker"


class ModelArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    sha256: str
    bytes: int = Field(ge=1)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or str(path) != value:
            raise ValueError("artifact path must be a normalized safe relative path")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("artifact SHA-256 must be 64 lowercase hex characters")
        return value


class ModelValidationSample(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    inputs: tuple[str, ...] = Field(min_length=1)
    expected_output_sha256: str

    @field_validator("expected_output_sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("sample digest must be 64 lowercase hex characters")
        return value


class LocalModelManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ModelKind
    repository: str
    revision: str
    artifacts: tuple[ModelArtifact, ...] = Field(min_length=2)
    model_path: str
    tokenizer_path: str
    dimension: int | None = Field(default=None, ge=1)
    max_sequence_tokens: int = Field(ge=2)
    pooling: Literal["attention_mask_mean"] | None = None
    normalize: bool
    precision: Literal["float32", "float16", "int8"]
    license: str = Field(min_length=1)
    trust_remote_code: Literal[False] = False
    acceptance_threshold: float | None = None
    validation_samples: tuple[ModelValidationSample, ...] = Field(min_length=1)

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        if _REPOSITORY.fullmatch(value) is None:
            raise ValueError("repository must be an owner/name identifier")
        return value

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        if _REVISION.fullmatch(value) is None:
            raise ValueError("revision must be an immutable 40-character commit hash")
        return value

    @model_validator(mode="after")
    def validate_kind_and_artifacts(self) -> "LocalModelManifest":
        paths = [artifact.path for artifact in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("artifact paths must be unique")
        if self.model_path not in paths or self.tokenizer_path not in paths:
            raise ValueError("model and tokenizer paths must name verified artifacts")
        if not self.model_path.endswith(".onnx"):
            raise ValueError("model artifact must be ONNX")
        if self.kind is ModelKind.EMBEDDING:
            if self.dimension is None or self.pooling is None or not self.normalize:
                raise ValueError("embedding manifest requires dimension, pooling, and normalization")
            if self.acceptance_threshold is not None:
                raise ValueError("embedding manifest cannot define a reranker threshold")
            if any(len(sample.inputs) != 1 for sample in self.validation_samples):
                raise ValueError("embedding validation samples require one query")
        else:
            if self.dimension is not None or self.pooling is not None:
                raise ValueError("reranker manifest cannot define embedding output shape")
            if self.acceptance_threshold is None or not math.isfinite(self.acceptance_threshold):
                raise ValueError("reranker manifest requires a finite calibrated threshold")
            if any(len(sample.inputs) != 2 for sample in self.validation_samples):
                raise ValueError("reranker validation samples require a query and passage")
        return self

    @computed_field
    @property
    def profile_id(self) -> str:
        payload = self.model_dump(mode="json", exclude={"profile_id"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def artifact(self, path: str) -> ModelArtifact:
        for artifact in self.artifacts:
            if artifact.path == path:
                return artifact
        raise KeyError(path)

    def canonical_json(self) -> str:
        payload = self.model_dump(mode="json", exclude={"profile_id"})
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


__all__ = [
    "LocalModelManifest",
    "ModelArtifact",
    "ModelKind",
    "ModelValidationSample",
    "canonical_output_sha256",
]
