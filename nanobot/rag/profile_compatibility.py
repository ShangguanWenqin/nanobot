"""Embedding profile compatibility and staged vector-generation transitions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Literal, Protocol

Pooling = Literal["attention_mask_mean", "cls"]
Precision = Literal["float32", "float16", "int8"]


@dataclass(frozen=True, slots=True)
class EmbeddingExecutionProfile:
    model_profile_id: str
    tokenizer_signature: str
    pooling: Pooling
    normalize: bool
    dimension: int
    precision: Precision
    provider: str
    validated_against_cpu: bool

    def __post_init__(self) -> None:
        for name, value in (
            ("model profile ID", self.model_profile_id),
            ("tokenizer signature", self.tokenizer_signature),
            ("provider", self.provider),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.dimension < 1:
            raise ValueError("embedding dimension must be positive")

    @property
    def compatibility_id(self) -> str:
        return _signature(self._persisted_payload())

    @property
    def profile_id(self) -> str:
        return _signature(asdict(self))

    def _persisted_payload(self) -> dict[str, object]:
        return {
            "dimension": self.dimension,
            "model_profile_id": self.model_profile_id,
            "normalize": self.normalize,
            "pooling": self.pooling,
            "precision": self.precision,
            "tokenizer_signature": self.tokenizer_signature,
        }


class ProfileAction(StrEnum):
    NOOP = "noop"
    REUSE = "reuse"
    REBUILD = "rebuild"


@dataclass(frozen=True, slots=True)
class ProfileTransition:
    action: ProfileAction
    reason: str
    from_profile_id: str
    to_profile_id: str

    def safe_status(self) -> dict[str, str]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "from_profile_id": self.from_profile_id,
            "to_profile_id": self.to_profile_id,
        }


def decide_embedding_transition(
    active: EmbeddingExecutionProfile,
    target: EmbeddingExecutionProfile,
) -> ProfileTransition:
    if active.profile_id == target.profile_id:
        return ProfileTransition(
            action=ProfileAction.NOOP,
            reason="identical_profile",
            from_profile_id=active.profile_id,
            to_profile_id=target.profile_id,
        )
    if active.compatibility_id == target.compatibility_id and target.validated_against_cpu:
        return ProfileTransition(
            action=ProfileAction.REUSE,
            reason="execution_provider_only",
            from_profile_id=active.profile_id,
            to_profile_id=target.profile_id,
        )
    return ProfileTransition(
        action=ProfileAction.REBUILD,
        reason="persisted_representation_changed",
        from_profile_id=active.profile_id,
        to_profile_id=target.profile_id,
    )


class StagedGenerationRepository(Protocol):
    async def build_staged_generation(self, profile: EmbeddingExecutionProfile) -> str: ...

    async def validate_staged_generation(
        self,
        generation_id: str,
        profile: EmbeddingExecutionProfile,
    ) -> bool: ...

    async def activate_generation(self, generation_id: str, profile_id: str) -> None: ...

    async def discard_staged_generation(self, generation_id: str) -> None: ...


class EmbeddingProfileCoordinator:
    def __init__(self, repository: StagedGenerationRepository) -> None:
        self.repository = repository

    async def apply(
        self,
        active: EmbeddingExecutionProfile,
        target: EmbeddingExecutionProfile,
    ) -> ProfileTransition:
        transition = decide_embedding_transition(active, target)
        if transition.action is not ProfileAction.REBUILD:
            return transition
        generation_id = await self.repository.build_staged_generation(target)
        try:
            valid = await self.repository.validate_staged_generation(generation_id, target)
            if not valid:
                raise RuntimeError("staged vector generation validation failed")
            await self.repository.activate_generation(generation_id, target.profile_id)
        except Exception:
            await self.repository.discard_staged_generation(generation_id)
            raise
        return transition


def _signature(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "EmbeddingExecutionProfile",
    "EmbeddingProfileCoordinator",
    "Pooling",
    "Precision",
    "ProfileAction",
    "ProfileTransition",
    "StagedGenerationRepository",
    "decide_embedding_transition",
]
