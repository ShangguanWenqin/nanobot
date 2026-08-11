"""Application-facing lifecycle boundary for optional local private RAG."""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from nanobot.rag.config import RagConfig
from nanobot.rag.protocols import LifecycleComponent


class RagAvailabilityStatus(StrEnum):
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    AVAILABLE = "available"


class RagManager:
    """Own optional RAG services without leaking them into the agent core."""

    def __init__(
        self,
        config: RagConfig,
        *,
        components: tuple[LifecycleComponent, ...] = (),
    ) -> None:
        self.config = config
        self._components = components
        self._started = False
        self._lifecycle_lock = asyncio.Lock()

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._started:
                return
            started: list[LifecycleComponent] = []
            try:
                for component in self._components:
                    await component.start()
                    started.append(component)
            except BaseException:
                for component in reversed(started):
                    await component.stop()
                raise
            self._started = True

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if not self._started:
                return
            try:
                for component in reversed(self._components):
                    await component.stop()
            finally:
                self._started = False


@dataclass(frozen=True, slots=True)
class RagSubsystem:
    """Conditional RAG construction result used by gateway integrations."""

    status: RagAvailabilityStatus
    manager: RagManager | None
    message: str
    missing_dependencies: tuple[str, ...] = ()

    async def start(self) -> None:
        if self.manager is not None:
            await self.manager.start()

    async def stop(self) -> None:
        if self.manager is not None:
            await self.manager.stop()


RagDependencyChecker = Callable[[], tuple[str, ...]]


def missing_rag_dependencies() -> tuple[str, ...]:
    """Return missing CPU-baseline modules without importing any of them."""

    modules = (
        "huggingface_hub",
        "jieba",
        "numpy",
        "onnxruntime",
        "tokenizers",
        "usearch",
    )
    return tuple(name for name in modules if importlib.util.find_spec(name) is None)


def create_rag_subsystem(
    config: RagConfig,
    *,
    dependency_checker: RagDependencyChecker = missing_rag_dependencies,
    components: tuple[LifecycleComponent, ...] = (),
) -> RagSubsystem:
    """Construct RAG only when enabled and all portable dependencies exist."""

    if not config.enabled:
        return RagSubsystem(
            status=RagAvailabilityStatus.DISABLED,
            manager=None,
            message="RAG is disabled",
        )

    missing = dependency_checker()
    if missing:
        names = ", ".join(missing)
        return RagSubsystem(
            status=RagAvailabilityStatus.UNAVAILABLE,
            manager=None,
            message=(
                "RAG is enabled but optional dependencies are missing "
                f"({names}); install nanobot-ai[rag]"
            ),
            missing_dependencies=missing,
        )

    return RagSubsystem(
        status=RagAvailabilityStatus.AVAILABLE,
        manager=RagManager(config, components=components),
        message="RAG is available",
    )


__all__ = [
    "RagAvailabilityStatus",
    "RagManager",
    "RagSubsystem",
    "create_rag_subsystem",
    "missing_rag_dependencies",
]
