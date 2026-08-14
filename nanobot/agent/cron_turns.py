"""Coordination for scheduled cron turns.
                         ┌─────────────────────┐
                         │   User Message      │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │   InboundMessage    │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │      loop.py        │
                         │   Agent Loop        │
                         └──────────┬──────────┘
                                    │
                     ┌──────────────┴──────────────┐
                     │                             │
                     ▼                             ▼
             当前 Turn 内消息                 新的 Automation Turn
                     │                             │
                     ▼                             ▼
              mid-turn injection        AutomationTurnCoordinator
                                                   │
                                     ┌─────────────┴─────────────┐
                                     │                           │
                                     ▼                           ▼
                             CronTurnCoordinator         其他 Automation
                                     │
                                     ▼
                              Cron metadata
                                     │
                         ┌───────────┴───────────┐
                         │                       │
                         ▼                       ▼
                      run_id                  job_id
                         │                       │
                    Turn 唯一 ID             Job 唯一 ID
                         │
                         ▼
                       Future
                         │
                         │ await
                         ▼
                  Agent Turn 完成
                         │
                         ▼
                  complete(response)
                         │
                         ▼
                  Future.set_result()
                         │
                         ▼
                  Cron 调用方继续
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable

from nanobot.agent.automation_turns import AutomationTurnCoordinator
from nanobot.bus.events import InboundMessage
from nanobot.cron.session_turns import (
    cron_run_id,
    cron_trigger,
    defer_cron_until_session_idle,
)

# 几乎没有实现新的调度机制，它只是把 AutomationTurnCoordinator 这个通用协调器“特化”为 Cron 场景。
# 它体现了一种架构思想，把“如何运行一个 Agent Turn”和“什么业务触发了这个 Agent Turn”解耦。
class CronTurnCoordinator(AutomationTurnCoordinator):
    """Manage scheduled cron turns without mixing them into live injections."""

    def __init__(
        self,
        *,
        publish_inbound: Callable[[InboundMessage], Awaitable[None]],
        dispatch: Callable[[InboundMessage], Awaitable[object]],
        is_running: Callable[[], bool],
        deferred_queues: dict[str, list[InboundMessage]] | None = None,
    ) -> None:
        super().__init__(
            publish_inbound=publish_inbound,
            dispatch=dispatch,
            is_running=is_running,
            turn_id=lambda msg: cron_run_id(msg.metadata), # 这里是cron的run_id,不是job_id
            pending_id=_cron_job_id, # 这里是job_id,因为pending_ids_for_session中使用pending_id，纬度是session
            should_defer_turn=_should_defer_cron_turn,
            missing_id_error="cron turn metadata must include a run_id",
            duplicate_id_error=lambda run_id: f"cron run {run_id!r} is already pending",
            deferred_queues=deferred_queues,
        )

    # 特意新构建一个函数，适配cron的情况
    def pending_job_ids_for_session(self, session_key: str) -> set[str]:
        """Return cron jobs that are waiting for or running in *session_key*."""
        return self.pending_ids_for_session(session_key)


# 如果session activate，推迟cron直到session空闲
def _should_defer_cron_turn(
    msg: InboundMessage,
    session_key: str,
    active_session_keys: Iterable[str],
) -> bool:
    return defer_cron_until_session_idle(msg.metadata) and session_key in active_session_keys

# 获取cron的job_id,一个定时任务只有一个
def _cron_job_id(msg: InboundMessage) -> str | None:
    trigger = cron_trigger(msg.metadata)
    if not trigger:
        return None
    value = trigger.get("job_id")
    return value if isinstance(value, str) and value else None
