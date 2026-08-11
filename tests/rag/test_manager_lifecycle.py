from __future__ import annotations

import pytest

from nanobot.rag.config import RagConfig
from nanobot.rag.manager import (
    RagAvailabilityStatus,
    create_rag_subsystem,
)


class FakeLifecycleComponent:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def start(self) -> None:
        self.calls.append("start")

    async def stop(self) -> None:
        self.calls.append("stop")


@pytest.mark.asyncio
async def test_disabled_rag_never_checks_or_constructs_optional_dependencies() -> None:
    def unexpected_check() -> tuple[str, ...]:
        raise AssertionError("disabled RAG must not inspect optional dependencies")

    subsystem = create_rag_subsystem(
        RagConfig(enabled=False), dependency_checker=unexpected_check
    )

    assert subsystem.status is RagAvailabilityStatus.DISABLED
    assert subsystem.manager is None
    await subsystem.start()
    await subsystem.stop()


@pytest.mark.asyncio
async def test_enabled_rag_with_missing_dependencies_is_safely_unavailable() -> None:
    subsystem = create_rag_subsystem(
        RagConfig(enabled=True),
        dependency_checker=lambda: ("onnxruntime", "usearch"),
    )

    assert subsystem.status is RagAvailabilityStatus.UNAVAILABLE
    assert subsystem.manager is None
    assert subsystem.missing_dependencies == ("onnxruntime", "usearch")
    assert "nanobot-ai[rag]" in subsystem.message
    await subsystem.start()
    await subsystem.stop()


@pytest.mark.asyncio
async def test_available_manager_starts_and_stops_components_once() -> None:
    component = FakeLifecycleComponent()
    subsystem = create_rag_subsystem(
        RagConfig(enabled=True),
        dependency_checker=tuple,
        components=(component,),
    )

    assert subsystem.status is RagAvailabilityStatus.AVAILABLE
    assert subsystem.manager is not None

    await subsystem.start()
    await subsystem.start()
    await subsystem.stop()
    await subsystem.stop()

    assert component.calls == ["start", "stop"]
    assert subsystem.manager.started is False
