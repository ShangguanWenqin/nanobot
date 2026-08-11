from __future__ import annotations

import asyncio
import time
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from nanobot.rag.config import RagParsingConfig
from nanobot.rag.parser import RagParseError
from nanobot.rag.parser_worker import parse_document_isolated
from nanobot.rag.types import RagErrorCode


def _slow_worker(
    connection: Connection,
    path: str,
    config: dict[str, object],
) -> None:
    del connection, path, config
    time.sleep(5)


def _crash_worker(
    connection: Connection,
    path: str,
    config: dict[str, object],
) -> None:
    del connection, path, config
    raise SystemExit(7)


async def test_isolated_parser_returns_structured_result(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")

    result = await parse_document_isolated(path, RagParsingConfig())

    assert result.blocks[0].text == "hello"


async def test_parser_timeout_does_not_block_asyncio_loop(tmp_path: Path) -> None:
    path = tmp_path / "slow.txt"
    path.write_text("hello", encoding="utf-8")
    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    heartbeat_task = asyncio.create_task(heartbeat())
    started = time.monotonic()
    try:
        with pytest.raises(RagParseError) as exc_info:
            await parse_document_isolated(
                path,
                RagParsingConfig(timeout_seconds=0.08),
                worker_entry=_slow_worker,
            )
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)

    assert exc_info.value.code is RagErrorCode.PARSE_TIMEOUT
    assert time.monotonic() - started < 1
    assert ticks >= 3


async def test_parser_crash_is_classified_and_cleaned_up(tmp_path: Path) -> None:
    path = tmp_path / "crash.txt"
    path.write_text("hello", encoding="utf-8")

    with pytest.raises(RagParseError) as exc_info:
        await parse_document_isolated(
            path,
            RagParsingConfig(timeout_seconds=1),
            worker_entry=_crash_worker,
        )

    assert exc_info.value.code is RagErrorCode.INTERNAL_ERROR
    assert "exit" not in exc_info.value.safe_message.lower()
