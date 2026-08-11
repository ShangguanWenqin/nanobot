"""Async process isolation for bounded RAG document parsing."""

from __future__ import annotations

import asyncio
import multiprocessing
from collections.abc import Callable
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Protocol, TypeAlias, cast

from nanobot.rag.config import RagParsingConfig
from nanobot.rag.parser import ParsedDocument, RagParseError, parse_document
from nanobot.rag.types import RagErrorCode

WorkerEntry: TypeAlias = Callable[[Connection, str, dict[str, object]], None]
_WorkerMessage: TypeAlias = (
    tuple[str, ParsedDocument]
    | tuple[str, str, str]
)


class _ProcessHandle(Protocol):
    def is_alive(self) -> bool: ...

    def terminate(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...


class _ClosableConnection(Protocol):
    def close(self) -> None: ...


def _worker_entry(
    connection: Connection,
    path: str,
    config_data: dict[str, object],
) -> None:
    try:
        config = RagParsingConfig.model_validate(config_data)
        connection.send(("ok", parse_document(path, config)))
    except RagParseError as exc:
        connection.send(("error", exc.code.value, exc.safe_message))
    except BaseException:
        connection.send(
            (
                "error",
                RagErrorCode.INTERNAL_ERROR.value,
                "文档解析进程发生内部错误",
            )
        )
    finally:
        connection.close()


async def _cleanup_process(
    process: _ProcessHandle,
    connection: _ClosableConnection,
) -> None:
    connection.close()
    if process.is_alive():
        process.terminate()
    await asyncio.to_thread(process.join, 0.5)


async def parse_document_isolated(
    path: str | Path,
    config: RagParsingConfig,
    *,
    worker_entry: WorkerEntry = _worker_entry,
) -> ParsedDocument:
    """Parse in a fresh child process without blocking the asyncio loop."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=worker_entry,
        args=(sender, str(path), cast(dict[str, object], config.model_dump())),
        name="nanobot-rag-parser",
        daemon=True,
    )
    try:
        process.start()
        sender.close()
        try:
            raw_message = await asyncio.wait_for(
                asyncio.to_thread(receiver.recv),
                timeout=config.timeout_seconds,
            )
        except TimeoutError as exc:
            raise RagParseError(
                RagErrorCode.PARSE_TIMEOUT,
                "文档解析超过时间上限",
            ) from exc
        except (EOFError, OSError) as exc:
            raise RagParseError(
                RagErrorCode.INTERNAL_ERROR,
                "文档解析进程意外终止",
            ) from exc

        message = cast(_WorkerMessage, raw_message)
        if message[0] == "ok":
            return cast(tuple[str, ParsedDocument], message)[1]
        error_message = cast(tuple[str, str, str], message)
        try:
            code = RagErrorCode(error_message[1])
        except ValueError:
            code = RagErrorCode.INTERNAL_ERROR
        raise RagParseError(code, error_message[2])
    finally:
        await _cleanup_process(process, receiver)


__all__ = ["WorkerEntry", "parse_document_isolated"]
