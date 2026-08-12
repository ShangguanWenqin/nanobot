from __future__ import annotations

import asyncio

import pytest

from nanobot.rag.inference_scheduler import PriorityInferenceScheduler


@pytest.mark.asyncio
async def test_interactive_work_runs_before_next_background_batch() -> None:
    scheduler = PriorityInferenceScheduler()
    first_batch_started = asyncio.Event()
    release_first_batch = asyncio.Event()
    calls: list[str] = []

    async def background(batch: tuple[str, ...]) -> tuple[str, ...]:
        calls.append(f"background:{','.join(batch)}")
        if batch == ("a", "b"):
            first_batch_started.set()
            await release_first_batch.wait()
        return batch

    async def interactive() -> str:
        calls.append("interactive")
        return "query-result"

    await scheduler.start()
    background_task = asyncio.create_task(
        scheduler.run_background_batches(
            ("a", "b", "c", "d"),
            batch_size=2,
            handler=background,
        )
    )
    await first_batch_started.wait()
    interactive_task = asyncio.create_task(scheduler.run_interactive(interactive))
    await asyncio.sleep(0)
    release_first_batch.set()

    assert await interactive_task == "query-result"
    assert await background_task == ("a", "b", "c", "d")
    assert calls == ["background:a,b", "interactive", "background:c,d"]
    await scheduler.stop()


@pytest.mark.asyncio
async def test_background_batches_are_bounded_and_yield_to_event_loop() -> None:
    scheduler = PriorityInferenceScheduler()
    batches: list[tuple[int, ...]] = []
    event_loop_ticks = 0

    async def handler(batch: tuple[int, ...]) -> tuple[int, ...]:
        nonlocal event_loop_ticks
        batches.append(batch)
        await asyncio.sleep(0)
        event_loop_ticks += 1
        return batch

    await scheduler.start()
    result = await scheduler.run_background_batches(
        tuple(range(7)), batch_size=3, handler=handler
    )
    await scheduler.stop()

    assert result == tuple(range(7))
    assert batches == [(0, 1, 2), (3, 4, 5), (6,)]
    assert event_loop_ticks == 3


@pytest.mark.asyncio
async def test_stopping_scheduler_rejects_new_work_and_cancels_cleanly() -> None:
    scheduler = PriorityInferenceScheduler()
    await scheduler.start()
    await scheduler.stop()

    async def interactive() -> str:
        return "never"

    with pytest.raises(RuntimeError, match="not running"):
        await scheduler.run_interactive(interactive)
