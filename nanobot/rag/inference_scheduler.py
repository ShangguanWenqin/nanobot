"""Query-first scheduling at bounded local-inference batch boundaries."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TypeVar, cast

T = TypeVar("T")
R = TypeVar("R")


@dataclass(order=True, slots=True)
class _ScheduledCall:
    priority: int
    sequence: int
    function: Callable[[], Awaitable[object]] = field(compare=False)
    future: asyncio.Future[object] = field(compare=False)


class PriorityInferenceScheduler:
    """Serialize inference while allowing queries ahead of later ingestion batches."""

    _INTERACTIVE = 0
    _BACKGROUND = 1

    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue[_ScheduledCall] = asyncio.PriorityQueue()
        self._worker: asyncio.Task[None] | None = None
        self._sequence = 0

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="nanobot-rag-inference")

    async def stop(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if not item.future.done():
                item.future.set_exception(RuntimeError("inference scheduler stopped"))
            self._queue.task_done()

    async def run_interactive(self, function: Callable[[], Awaitable[R]]) -> R:
        return cast(R, await self._submit(self._INTERACTIVE, function))

    async def run_background_batches(
        self,
        items: Sequence[T],
        *,
        batch_size: int,
        handler: Callable[[tuple[T, ...]], Awaitable[Sequence[R]]],
    ) -> tuple[R, ...]:
        if batch_size < 1:
            raise ValueError("inference batch size must be positive")
        results: list[R] = []
        for start in range(0, len(items), batch_size):
            batch = tuple(items[start : start + batch_size])

            async def run_batch(batch: tuple[T, ...] = batch) -> object:
                return await handler(batch)

            result = await self._submit(self._BACKGROUND, run_batch)
            results.extend(cast(Sequence[R], result))
            await asyncio.sleep(0)
        return tuple(results)

    async def _submit(
        self,
        priority: int,
        function: Callable[[], Awaitable[object]],
    ) -> object:
        if self._worker is None:
            await _close_unstarted_awaitable(function)
            raise RuntimeError("inference scheduler is not running")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[object] = loop.create_future()
        self._sequence += 1
        await self._queue.put(_ScheduledCall(priority, self._sequence, function, future))
        return await future

    async def _run(self) -> None:
        while True:
            call = await self._queue.get()
            try:
                if call.future.cancelled():
                    continue
                result = await call.function()
                if not call.future.done():
                    call.future.set_result(result)
            except BaseException as exc:
                if not call.future.done():
                    call.future.set_exception(exc)
            finally:
                self._queue.task_done()


async def _close_unstarted_awaitable(function: Callable[[], Awaitable[object]]) -> None:
    awaitable = function()
    if inspect.iscoroutine(awaitable):
        awaitable.close()


__all__ = ["PriorityInferenceScheduler"]
