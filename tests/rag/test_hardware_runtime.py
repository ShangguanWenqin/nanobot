from __future__ import annotations

from dataclasses import replace

import pytest

from nanobot.rag.hardware import (
    AcceleratorInfo,
    HardwareProbeSource,
    HardwareSnapshot,
    RuntimeCandidate,
    Workload,
    build_runtime_candidates,
    probe_hardware,
)


def _snapshot(**overrides: object) -> HardwareSnapshot:
    data: dict[str, object] = {
        "os_name": "Darwin",
        "os_version": "25.6.0",
        "architecture": "arm64",
        "cpu_model": "Apple M4 Pro",
        "logical_cpu_count": 14,
        "memory_bytes": 48 * 1024**3,
        "accelerators": (
            AcceleratorInfo(kind="apple_gpu", name="Apple M4 Pro", memory_bytes=None),
        ),
        "execution_providers": ("CPUExecutionProvider", "CoreMLExecutionProvider"),
        "provider_versions": (("onnxruntime", "1.28.0"),),
        "driver_versions": (("coreml", "25.6.0"),),
        "runtime_versions": (("python", "3.12.12"), ("nanobot", "0.3.0")),
        "model_profile_ids": ("embedding-sha", "reranker-sha"),
    }
    data.update(overrides)
    return HardwareSnapshot(**data)  # type: ignore[arg-type]


def test_deterministic_hardware_probe_captures_complete_versioned_fingerprint() -> None:
    source = HardwareProbeSource(
        os_name=lambda: "Linux",
        os_version=lambda: "6.8.0",
        architecture=lambda: "x86_64",
        cpu_model=lambda: "Intel Xeon 8480+",
        logical_cpu_count=lambda: 112,
        memory_bytes=lambda: 256 * 1024**3,
        accelerators=lambda: (
            AcceleratorInfo(
                kind="nvidia_gpu",
                name="NVIDIA L4",
                memory_bytes=24 * 1024**3,
            ),
        ),
        execution_providers=lambda: (
            "CPUExecutionProvider",
            "CUDAExecutionProvider",
        ),
        provider_versions=lambda: (
            ("onnxruntime-gpu", "1.22.0"),
            ("CUDAExecutionProvider", "12.6"),
        ),
        driver_versions=lambda: (("nvidia", "560.35.03"),),
        runtime_versions=lambda: (("python", "3.12.12"), ("nanobot", "0.3.0")),
    )

    snapshot = probe_hardware(source, model_profile_ids=("embed-a", "rerank-b"))

    assert snapshot.os_name == "Linux"
    assert snapshot.architecture == "x86_64"
    assert snapshot.cpu_model == "Intel Xeon 8480+"
    assert snapshot.memory_bytes == 256 * 1024**3
    assert snapshot.accelerators[0].name == "NVIDIA L4"
    assert snapshot.execution_providers[-1] == "CUDAExecutionProvider"
    assert len(snapshot.fingerprint) == 64
    assert snapshot.fingerprint == probe_hardware(
        source, model_profile_ids=("embed-a", "rerank-b")
    ).fingerprint
    assert snapshot.fingerprint != replace(
        snapshot,
        driver_versions=(("nvidia", "561.00"),),
    ).fingerprint
    assert snapshot.fingerprint != replace(
        snapshot,
        model_profile_ids=("embed-c", "rerank-b"),
    ).fingerprint


def test_snapshot_canonicalizes_order_and_rejects_invalid_probe_values() -> None:
    first = _snapshot(
        execution_providers=("CoreMLExecutionProvider", "CPUExecutionProvider"),
        runtime_versions=(("nanobot", "0.3.0"), ("python", "3.12.12")),
    )
    second = _snapshot()

    assert first.fingerprint == second.fingerprint
    with pytest.raises(ValueError, match="memory"):
        _snapshot(memory_bytes=-1)
    with pytest.raises(ValueError, match="provider"):
        _snapshot(execution_providers=("",))
    with pytest.raises(ValueError, match="model profile"):
        _snapshot(model_profile_ids=())


def test_candidate_matrix_uses_only_installed_platform_compatible_providers() -> None:
    candidates = build_runtime_candidates(_snapshot(), mode="auto")

    assert candidates == (
        RuntimeCandidate(
            name="cpu-float32",
            provider="CPUExecutionProvider",
            precision="float32",
            workloads=(
                Workload.QUERY_EMBEDDING,
                Workload.BATCH_EMBEDDING,
                Workload.RERANKER,
            ),
        ),
        RuntimeCandidate(
            name="coreml-float32",
            provider="CoreMLExecutionProvider",
            precision="float32",
            workloads=(
                Workload.QUERY_EMBEDDING,
                Workload.BATCH_EMBEDDING,
                Workload.RERANKER,
            ),
        ),
    )


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (
            _snapshot(
                os_name="Linux",
                architecture="x86_64",
                accelerators=(
                    AcceleratorInfo("nvidia_gpu", "RTX 4090", 24 * 1024**3),
                ),
                execution_providers=(
                    "CPUExecutionProvider",
                    "CUDAExecutionProvider",
                    "OpenVINOExecutionProvider",
                ),
            ),
            {"cpu-float32", "cuda-float32", "openvino-float32"},
        ),
        (
            _snapshot(
                os_name="Windows",
                architecture="AMD64",
                accelerators=(AcceleratorInfo("windows_gpu", "Arc A770", None),),
                execution_providers=(
                    "CPUExecutionProvider",
                    "DmlExecutionProvider",
                    "CoreMLExecutionProvider",
                ),
            ),
            {"cpu-float32", "directml-float32"},
        ),
    ],
)
def test_candidate_matrix_supports_cuda_openvino_and_directml(
    snapshot: HardwareSnapshot,
    expected: set[str],
) -> None:
    assert {candidate.name for candidate in build_runtime_candidates(snapshot)} == expected


def test_detected_but_uninstalled_or_wrong_platform_provider_is_not_a_candidate() -> None:
    snapshot = _snapshot(
        os_name="Linux",
        architecture="aarch64",
        accelerators=(
            AcceleratorInfo("nvidia_gpu", "NVIDIA GPU", None),
            AcceleratorInfo("apple_gpu", "Misreported Apple GPU", None),
        ),
        execution_providers=("CPUExecutionProvider",),
    )

    assert [candidate.name for candidate in build_runtime_candidates(snapshot)] == [
        "cpu-float32"
    ]


def test_forced_mode_requires_an_installed_compatible_provider() -> None:
    assert [
        candidate.name for candidate in build_runtime_candidates(_snapshot(), mode="cpu")
    ] == ["cpu-float32"]
    assert [
        candidate.name
        for candidate in build_runtime_candidates(_snapshot(), mode="coreml")
    ] == ["coreml-float32"]

    with pytest.raises(ValueError, match="unavailable"):
        build_runtime_candidates(_snapshot(), mode="cuda")
    with pytest.raises(ValueError, match="unavailable"):
        build_runtime_candidates(
            _snapshot(
                os_name="Linux",
                architecture="aarch64",
                execution_providers=(
                    "CPUExecutionProvider",
                    "CoreMLExecutionProvider",
                ),
            ),
            mode="coreml",
        )


def test_no_installed_onnx_provider_is_explicitly_unavailable() -> None:
    snapshot = _snapshot(execution_providers=())

    with pytest.raises(ValueError, match="no compatible installed"):
        build_runtime_candidates(snapshot)
