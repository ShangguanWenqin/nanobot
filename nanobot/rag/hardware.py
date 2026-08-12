"""Deterministic hardware fingerprints and installed ONNX provider candidates."""

from __future__ import annotations

import hashlib
import json
import os
import platform
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from importlib import import_module, metadata
from typing import Literal, cast

from nanobot.rag.config import RagRuntimeMode

Precision = Literal["float32", "float16", "int8"]


class Workload(StrEnum):
    QUERY_EMBEDDING = "query_embedding"
    BATCH_EMBEDDING = "batch_embedding"
    RERANKER = "reranker"


@dataclass(frozen=True, slots=True)
class AcceleratorInfo:
    kind: str
    name: str
    memory_bytes: int | None

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.name.strip():
            raise ValueError("accelerator kind and name must not be empty")
        if self.memory_bytes is not None and self.memory_bytes < 0:
            raise ValueError("accelerator memory must not be negative")


@dataclass(frozen=True, slots=True)
class HardwareSnapshot:
    os_name: str
    os_version: str
    architecture: str
    cpu_model: str
    logical_cpu_count: int
    memory_bytes: int
    accelerators: tuple[AcceleratorInfo, ...]
    execution_providers: tuple[str, ...]
    provider_versions: tuple[tuple[str, str], ...]
    driver_versions: tuple[tuple[str, str], ...]
    runtime_versions: tuple[tuple[str, str], ...]
    model_profile_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, value in (
            ("operating system", self.os_name),
            ("operating system version", self.os_version),
            ("architecture", self.architecture),
            ("CPU model", self.cpu_model),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.logical_cpu_count < 1:
            raise ValueError("logical CPU count must be positive")
        if self.memory_bytes < 0:
            raise ValueError("memory bytes must not be negative")
        if any(not provider.strip() for provider in self.execution_providers):
            raise ValueError("execution provider names must not be empty")
        if not self.model_profile_ids or any(
            not profile.strip() for profile in self.model_profile_ids
        ):
            raise ValueError("model profile IDs must not be empty")
        self._validate_versions("provider", self.provider_versions)
        self._validate_versions("driver", self.driver_versions)
        self._validate_versions("runtime", self.runtime_versions)
        object.__setattr__(
            self,
            "accelerators",
            tuple(sorted(self.accelerators, key=lambda item: (item.kind, item.name))),
        )
        object.__setattr__(
            self,
            "execution_providers",
            tuple(sorted(set(self.execution_providers))),
        )
        object.__setattr__(self, "provider_versions", _sorted_pairs(self.provider_versions))
        object.__setattr__(self, "driver_versions", _sorted_pairs(self.driver_versions))
        object.__setattr__(self, "runtime_versions", _sorted_pairs(self.runtime_versions))
        object.__setattr__(self, "model_profile_ids", tuple(sorted(self.model_profile_ids)))

    @staticmethod
    def _validate_versions(name: str, pairs: tuple[tuple[str, str], ...]) -> None:
        if any(not key.strip() or not value.strip() for key, value in pairs):
            raise ValueError(f"{name} version names and values must not be empty")
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError(f"{name} version names must be unique")

    @property
    def fingerprint(self) -> str:
        payload = asdict(self)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class HardwareProbeSource:
    os_name: Callable[[], str]
    os_version: Callable[[], str]
    architecture: Callable[[], str]
    cpu_model: Callable[[], str]
    logical_cpu_count: Callable[[], int]
    memory_bytes: Callable[[], int]
    accelerators: Callable[[], tuple[AcceleratorInfo, ...]]
    execution_providers: Callable[[], tuple[str, ...]]
    provider_versions: Callable[[], tuple[tuple[str, str], ...]]
    driver_versions: Callable[[], tuple[tuple[str, str], ...]]
    runtime_versions: Callable[[], tuple[tuple[str, str], ...]]


@dataclass(frozen=True, slots=True)
class RuntimeCandidate:
    name: str
    provider: str
    precision: Precision
    workloads: tuple[Workload, ...]


_ALL_WORKLOADS = (
    Workload.QUERY_EMBEDDING,
    Workload.BATCH_EMBEDDING,
    Workload.RERANKER,
)

_CANDIDATES: tuple[tuple[RagRuntimeMode, str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("cpu", "cpu-float32", "CPUExecutionProvider", (), ()),
    ("coreml", "coreml-float32", "CoreMLExecutionProvider", ("darwin",), ("arm64",)),
    (
        "cuda",
        "cuda-float32",
        "CUDAExecutionProvider",
        ("linux", "windows"),
        ("x86_64", "amd64", "aarch64", "arm64"),
    ),
    (
        "openvino",
        "openvino-float32",
        "OpenVINOExecutionProvider",
        ("linux", "windows"),
        ("x86_64", "amd64"),
    ),
    (
        "directml",
        "directml-float32",
        "DmlExecutionProvider",
        ("windows",),
        ("x86_64", "amd64", "arm64"),
    ),
)


def probe_hardware(
    source: HardwareProbeSource,
    *,
    model_profile_ids: tuple[str, ...],
) -> HardwareSnapshot:
    return HardwareSnapshot(
        os_name=source.os_name(),
        os_version=source.os_version(),
        architecture=source.architecture(),
        cpu_model=source.cpu_model(),
        logical_cpu_count=source.logical_cpu_count(),
        memory_bytes=source.memory_bytes(),
        accelerators=source.accelerators(),
        execution_providers=source.execution_providers(),
        provider_versions=source.provider_versions(),
        driver_versions=source.driver_versions(),
        runtime_versions=source.runtime_versions(),
        model_profile_ids=model_profile_ids,
    )


def build_runtime_candidates(
    snapshot: HardwareSnapshot,
    *,
    mode: RagRuntimeMode = "auto",
) -> tuple[RuntimeCandidate, ...]:
    candidates: list[RuntimeCandidate] = []
    os_name = snapshot.os_name.casefold()
    architecture = snapshot.architecture.casefold()
    installed = set(snapshot.execution_providers)
    for candidate_mode, name, provider, operating_systems, architectures in _CANDIDATES:
        if mode != "auto" and candidate_mode != mode:
            continue
        if provider not in installed:
            continue
        if operating_systems and os_name not in operating_systems:
            continue
        if architectures and architecture not in architectures:
            continue
        candidates.append(
            RuntimeCandidate(
                name=name,
                provider=provider,
                precision="float32",
                workloads=_ALL_WORKLOADS,
            )
        )
    if mode != "auto" and not candidates:
        raise ValueError(f"forced RAG runtime mode {mode!r} is unavailable on this host")
    if mode == "auto" and not candidates:
        raise ValueError("no compatible installed local ONNX execution provider is available")
    return tuple(candidates)


def default_probe_source() -> HardwareProbeSource:
    """Build a side-effect-free probe using only already installed local runtimes."""

    return HardwareProbeSource(
        os_name=platform.system,
        os_version=platform.release,
        architecture=platform.machine,
        cpu_model=lambda: platform.processor() or platform.machine() or "unknown-cpu",
        logical_cpu_count=lambda: os.cpu_count() or 1,
        memory_bytes=_system_memory_bytes,
        accelerators=_local_accelerators,
        execution_providers=_installed_execution_providers,
        provider_versions=_provider_versions,
        driver_versions=_local_driver_versions,
        runtime_versions=_runtime_versions,
    )


def _system_memory_bytes() -> int:
    if hasattr(os, "sysconf"):
        try:
            pages = int(os.sysconf("SC_PHYS_PAGES"))
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            return max(pages * page_size, 0)
        except (OSError, ValueError):
            pass
    return 0


def _installed_execution_providers() -> tuple[str, ...]:
    try:
        module = import_module("onnxruntime")
        get_available = cast(Callable[[], list[str]], getattr(module, "get_available_providers"))
        return tuple(get_available())
    except (ImportError, AttributeError):
        return ()


def _provider_versions() -> tuple[tuple[str, str], ...]:
    versions: list[tuple[str, str]] = []
    for distribution in (
        "onnxruntime",
        "onnxruntime-gpu",
        "onnxruntime-openvino",
        "onnxruntime-directml",
    ):
        try:
            versions.append((distribution, metadata.version(distribution)))
        except metadata.PackageNotFoundError:
            continue
    return tuple(versions) or (("onnxruntime", "unavailable"),)


def _runtime_versions() -> tuple[tuple[str, str], ...]:
    try:
        nanobot_version = metadata.version("nanobot-ai")
    except metadata.PackageNotFoundError:
        nanobot_version = "source"
    return (("nanobot", nanobot_version), ("python", platform.python_version()))


def _local_accelerators() -> tuple[AcceleratorInfo, ...]:
    providers = set(_installed_execution_providers())
    accelerators: list[AcceleratorInfo] = []
    if "CoreMLExecutionProvider" in providers:
        accelerators.append(AcceleratorInfo("apple_gpu", platform.processor() or "Apple GPU", None))
    if "CUDAExecutionProvider" in providers:
        accelerators.append(AcceleratorInfo("nvidia_gpu", "CUDA device", None))
    if "DmlExecutionProvider" in providers:
        accelerators.append(AcceleratorInfo("windows_gpu", "DirectML device", None))
    return tuple(accelerators)


def _local_driver_versions() -> tuple[tuple[str, str], ...]:
    # Provider packages expose the stable runtime compatibility boundary without
    # spawning vendor tools, which may hang or disclose device topology.
    return tuple(
        (provider, version)
        for provider, version in _provider_versions()
        if provider != "onnxruntime"
    ) or (("operating_system", platform.release()),)


def _sorted_pairs(pairs: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(pairs, key=lambda item: item[0]))


__all__ = [
    "AcceleratorInfo",
    "HardwareProbeSource",
    "HardwareSnapshot",
    "Precision",
    "RuntimeCandidate",
    "Workload",
    "build_runtime_candidates",
    "default_probe_source",
    "probe_hardware",
]
