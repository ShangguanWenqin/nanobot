from __future__ import annotations

import pytest

from nanobot.rag.builtin_models import (
    BGE_RERANKER_BASE,
    BUILTIN_MODEL_MANIFESTS,
    MULTILINGUAL_E5_SMALL,
    get_builtin_manifest,
)
from nanobot.rag.model_manifest import ModelKind


def test_embedding_manifest_pins_audited_onnx_and_validation_output() -> None:
    manifest = MULTILINGUAL_E5_SMALL

    assert manifest.kind is ModelKind.EMBEDDING
    assert manifest.repository == "intfloat/multilingual-e5-small"
    assert manifest.revision == "5697a65b0a002a92fe8c4fc9d495303ffff9c7d2"
    assert manifest.dimension == 384
    assert manifest.pooling == "attention_mask_mean"
    assert manifest.normalize is True
    assert manifest.precision == "float32"
    assert manifest.license == "MIT"
    assert manifest.trust_remote_code is False
    assert manifest.artifact("onnx/model.onnx").model_dump() == {
        "path": "onnx/model.onnx",
        "sha256": "ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665",
        "bytes": 470_268_510,
    }
    assert manifest.validation_samples[0].expected_output_sha256 == (
        "e28f38dc2b01a0a90b1a73bafa4c1d0f77e4b44fe3681ac707863823c994e0b6"
    )


def test_reranker_manifest_pins_audited_onnx_tokenizer_and_threshold() -> None:
    manifest = BGE_RERANKER_BASE

    assert manifest.kind is ModelKind.RERANKER
    assert manifest.repository == "BAAI/bge-reranker-base"
    assert manifest.revision == "711afb1eff814a80f5363996cd76e1b5f39cc7d7"
    assert manifest.acceptance_threshold == 0.5
    assert manifest.normalize is False
    assert manifest.artifact("onnx/model.onnx").bytes == 1_112_459_588
    assert manifest.artifact("tokenizer.json").sha256 == (
        "9eb652ac4e40cc093272bbbe0f55d521cf67570060227109b5cdc20945a4489e"
    )
    assert manifest.validation_samples[0].expected_output_sha256 == (
        "4694bf1e18d02eb030fa910e8a7cd143eda5091fa18cfb0c0850d9531a4e6051"
    )


def test_builtin_profiles_are_named_by_config_defaults_and_immutable() -> None:
    assert get_builtin_manifest("multilingual-e5-small-v1") is MULTILINGUAL_E5_SMALL
    assert get_builtin_manifest("bge-reranker-base-v1") is BGE_RERANKER_BASE
    assert len(BUILTIN_MODEL_MANIFESTS) == 2

    with pytest.raises(ValueError, match="unknown built-in"):
        get_builtin_manifest("main")
