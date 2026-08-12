from __future__ import annotations

from dataclasses import dataclass

import pytest

from nanobot.rag.profile_compatibility import (
    EmbeddingExecutionProfile,
    EmbeddingProfileCoordinator,
    ProfileAction,
    ProfileTransition,
    decide_embedding_transition,
)


def _profile(**overrides: object) -> EmbeddingExecutionProfile:
    data: dict[str, object] = {
        "model_profile_id": "model-sha",
        "tokenizer_signature": "tokenizer-sha",
        "pooling": "attention_mask_mean",
        "normalize": True,
        "dimension": 384,
        "precision": "float32",
        "provider": "CPUExecutionProvider",
        "validated_against_cpu": True,
    }
    data.update(overrides)
    return EmbeddingExecutionProfile(**data)  # type: ignore[arg-type]


def test_execution_device_change_reuses_vectors_only_after_validation() -> None:
    active = _profile()
    coreml = _profile(provider="CoreMLExecutionProvider")

    transition = decide_embedding_transition(active, coreml)

    assert transition.action is ProfileAction.REUSE
    assert transition.reason == "execution_provider_only"
    assert transition.from_profile_id == active.profile_id
    assert transition.to_profile_id == coreml.profile_id
    assert active.compatibility_id == coreml.compatibility_id

    unvalidated = _profile(
        provider="CoreMLExecutionProvider",
        validated_against_cpu=False,
    )
    assert decide_embedding_transition(active, unvalidated).action is ProfileAction.REBUILD


@pytest.mark.parametrize(
    "overrides",
    [
        {"model_profile_id": "new-model"},
        {"tokenizer_signature": "new-tokenizer"},
        {"pooling": "cls"},
        {"normalize": False},
        {"dimension": 768},
        {"precision": "int8"},
    ],
)
def test_persisted_representation_change_requires_rebuild(
    overrides: dict[str, object],
) -> None:
    transition = decide_embedding_transition(_profile(), _profile(**overrides))

    assert transition.action is ProfileAction.REBUILD
    assert transition.reason == "persisted_representation_changed"


def test_identical_profile_is_noop_and_profile_signatures_are_stable() -> None:
    first = _profile()
    second = _profile()

    transition = decide_embedding_transition(first, second)

    assert transition.action is ProfileAction.NOOP
    assert len(first.profile_id) == 64
    assert len(first.compatibility_id) == 64
    assert first.profile_id == second.profile_id


@dataclass
class FakeGenerationRepository:
    active_profile_id: str
    built: list[str]
    validated: list[str]
    activated: list[str]
    discarded: list[str]
    valid: bool = True

    async def build_staged_generation(self, profile: EmbeddingExecutionProfile) -> str:
        self.built.append(profile.profile_id)
        return "generation-new"

    async def validate_staged_generation(
        self,
        generation_id: str,
        profile: EmbeddingExecutionProfile,
    ) -> bool:
        del profile
        self.validated.append(generation_id)
        return self.valid

    async def activate_generation(self, generation_id: str, profile_id: str) -> None:
        self.activated.append(generation_id)
        self.active_profile_id = profile_id

    async def discard_staged_generation(self, generation_id: str) -> None:
        self.discarded.append(generation_id)


@pytest.mark.asyncio
async def test_coordinator_builds_validates_then_atomically_activates_rebuild() -> None:
    repository = FakeGenerationRepository("old", [], [], [], [])
    target = _profile(model_profile_id="new-model")
    coordinator = EmbeddingProfileCoordinator(repository)

    transition = await coordinator.apply(_profile(), target)

    assert transition.action is ProfileAction.REBUILD
    assert repository.built == [target.profile_id]
    assert repository.validated == ["generation-new"]
    assert repository.activated == ["generation-new"]
    assert repository.active_profile_id == target.profile_id
    assert repository.discarded == []


@pytest.mark.asyncio
async def test_failed_staged_generation_is_discarded_without_switching_active() -> None:
    repository = FakeGenerationRepository("old", [], [], [], [], valid=False)
    coordinator = EmbeddingProfileCoordinator(repository)

    with pytest.raises(RuntimeError, match="validation"):
        await coordinator.apply(_profile(), _profile(precision="int8"))

    assert repository.active_profile_id == "old"
    assert repository.activated == []
    assert repository.discarded == ["generation-new"]


@pytest.mark.asyncio
async def test_reuse_and_noop_do_not_rebuild_persisted_vectors() -> None:
    repository = FakeGenerationRepository("old", [], [], [], [])
    coordinator = EmbeddingProfileCoordinator(repository)

    reused = await coordinator.apply(
        _profile(),
        _profile(provider="CoreMLExecutionProvider"),
    )
    noop = await coordinator.apply(_profile(), _profile())

    assert reused.action is ProfileAction.REUSE
    assert noop.action is ProfileAction.NOOP
    assert repository.built == []
    assert repository.activated == []


def test_transition_is_safe_status_data_without_host_paths() -> None:
    transition = ProfileTransition(
        action=ProfileAction.REUSE,
        reason="execution_provider_only",
        from_profile_id="from",
        to_profile_id="to",
    )

    assert transition.safe_status() == {
        "action": "reuse",
        "reason": "execution_provider_only",
        "from_profile_id": "from",
        "to_profile_id": "to",
    }
