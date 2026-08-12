"""Audited immutable manifests for the default local RAG models."""

from __future__ import annotations

from nanobot.rag.model_manifest import LocalModelManifest

MULTILINGUAL_E5_SMALL = LocalModelManifest.model_validate(
    {
        "kind": "embedding",
        "repository": "intfloat/multilingual-e5-small",
        "revision": "5697a65b0a002a92fe8c4fc9d495303ffff9c7d2",
        "artifacts": [
            {
                "path": "onnx/model.onnx",
                "sha256": "ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665",
                "bytes": 470_268_510,
            },
            {
                "path": "onnx/tokenizer.json",
                "sha256": "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
                "bytes": 17_082_730,
            },
        ],
        "model_path": "onnx/model.onnx",
        "tokenizer_path": "onnx/tokenizer.json",
        "dimension": 384,
        "max_sequence_tokens": 512,
        "pooling": "attention_mask_mean",
        "normalize": True,
        "precision": "float32",
        "license": "MIT",
        "trust_remote_code": False,
        "validation_samples": [
            {
                "inputs": ["nanobot 支持私有知识库检索。"],
                "expected_output_sha256": (
                    "ed2cd9707fbd01f027aff3e59402946fd290d39c1318d5bfc92026e6dbe1d529"
                ),
            }
        ],
    }
)


BGE_RERANKER_BASE = LocalModelManifest.model_validate(
    {
        "kind": "reranker",
        "repository": "BAAI/bge-reranker-base",
        "revision": "711afb1eff814a80f5363996cd76e1b5f39cc7d7",
        "artifacts": [
            {
                "path": "onnx/model.onnx",
                "sha256": "15b9a8c3da82eddf263df571281166e00e9308fe19d077084b642ebfcaf06d2b",
                "bytes": 1_112_459_588,
            },
            {
                "path": "tokenizer.json",
                "sha256": "9eb652ac4e40cc093272bbbe0f55d521cf67570060227109b5cdc20945a4489e",
                "bytes": 17_098_107,
            },
        ],
        "model_path": "onnx/model.onnx",
        "tokenizer_path": "tokenizer.json",
        "max_sequence_tokens": 512,
        "normalize": False,
        "precision": "float32",
        "license": "MIT",
        "trust_remote_code": False,
        # Bootstrap operating threshold. Task 9.6 regenerates this value from the
        # versioned bilingual evaluation corpus before the release quality gate.
        "acceptance_threshold": 0.5,
        "validation_samples": [
            {
                "inputs": [
                    "nanobot 支持什么功能？",
                    "nanobot 支持私有知识库检索。",
                ],
                "expected_output_sha256": (
                    "4694bf1e18d02eb030fa910e8a7cd143eda5091fa18cfb0c0850d9531a4e6051"
                ),
            }
        ],
    }
)


BUILTIN_MODEL_MANIFESTS = {
    "multilingual-e5-small-v1": MULTILINGUAL_E5_SMALL,
    "bge-reranker-base-v1": BGE_RERANKER_BASE,
}


def get_builtin_manifest(profile_name: str) -> LocalModelManifest:
    try:
        return BUILTIN_MODEL_MANIFESTS[profile_name]
    except KeyError as exc:
        raise ValueError(f"unknown built-in RAG model profile: {profile_name}") from exc


__all__ = [
    "BGE_RERANKER_BASE",
    "BUILTIN_MODEL_MANIFESTS",
    "MULTILINGUAL_E5_SMALL",
    "get_builtin_manifest",
]
