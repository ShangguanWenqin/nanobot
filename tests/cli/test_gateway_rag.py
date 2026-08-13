from __future__ import annotations

import builtins

import pytest

from nanobot.cli.gateway_runtime import _initialize_gateway_rag
from nanobot.config.schema import Config
from nanobot.rag.manager import RagAvailabilityStatus


@pytest.mark.asyncio
async def test_disabled_gateway_rag_does_not_import_optional_runtime(monkeypatch) -> None:
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "nanobot.rag.bootstrap":
            raise AssertionError("disabled RAG must not import the optional runtime")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    subsystem, application = await _initialize_gateway_rag(Config(), object())

    assert subsystem.status is RagAvailabilityStatus.DISABLED
    assert application is None


@pytest.mark.asyncio
async def test_gateway_rag_preparation_failure_degrades_without_crashing(monkeypatch) -> None:
    config = Config()
    config.rag.enabled = True
    monkeypatch.setattr(
        "nanobot.rag.manager.missing_rag_dependencies",
        lambda: (),
    )
    monkeypatch.setattr(
        "nanobot.rag.manager.create_rag_subsystem",
        lambda rag_config: __import__("nanobot.rag.manager", fromlist=["RagSubsystem"]).RagSubsystem(
            status=RagAvailabilityStatus.AVAILABLE,
            manager=None,
            message="available",
        ),
    )
    monkeypatch.setattr(
        "nanobot.rag.bootstrap.prepare_local_rag_runtime",
        lambda _config: _raise_runtime_error(),
    )

    subsystem, application = await _initialize_gateway_rag(config, object())

    assert subsystem.status is RagAvailabilityStatus.UNAVAILABLE
    assert application is None


async def _raise_runtime_error():
    raise RuntimeError("model failed")
