"""Async cancellation helpers."""

from __future__ import annotations

import asyncio


# 读取当前任务的取消状态供清理路径使用，不消费或改变取消请求。
def task_is_cancelling() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0
