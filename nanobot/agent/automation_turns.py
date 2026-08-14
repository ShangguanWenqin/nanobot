"""Shared coordination for session-bound automation turns.
                    ┌──────────────────────┐
                    │   Automation System  │
                    │ cron / heartbeat ... │
                    └──────────┬───────────┘
                               │
                               │ InboundMessage
                               ▼
                  ┌──────────────────────────┐
                  │ AutomationTurnCoordinator│
                  │                          │
                  │  submit()                │
                  │  defer_if_active()       │
                  │  complete()              │
                  └────────────┬─────────────┘
                               │
               ┌───────────────┴───────────────┐
               │                               │
        session 空闲                      session 正在运行
               │                               │
               ▼                               ▼
         _dispatch(msg)              deferred_queues[session]
               │                               │
               ▼                               │
        正常 Agent Turn                        │
                                               │
                              当前 Turn 完成后 │
                                               ▼
                                  publish_next_deferred()
                                               │
                                               ▼
                                      再次 publish inbound
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable, Iterable

from nanobot.bus.events import InboundMessage, OutboundMessage


class AutomationTurnError(RuntimeError):
    """Raised when an automation turn reaches the agent and finishes with an error."""


# 将某个sessionndeferred队列中的第一条消息发送到inbound
async def publish_next_deferred_turn(
    *,
    deferred_queues: dict[str, list[InboundMessage]],
    publish_inbound: Callable[[InboundMessage], Awaitable[None]],
    session_key: str,
) -> bool:
    """Publish the next deferred automation turn for a session."""
    queue = deferred_queues.get(session_key)
    if not queue:
        return False
    msg = queue.pop(0)
    if not queue:
        deferred_queues.pop(session_key, None)
    await publish_inbound(msg)
    return True


# 管理cron等自动触发的消息turn，不会把它插入到mid injection中，而是把它当作一个新的turn
class AutomationTurnCoordinator:
    """Manage automation turns without mixing them into live injections."""

    def __init__(
        self,
        *,
        publish_inbound: Callable[[InboundMessage], Awaitable[None]],
        dispatch: Callable[[InboundMessage], Awaitable[object]],
        is_running: Callable[[], bool],
        turn_id: Callable[[InboundMessage], str | None],
        pending_id: Callable[[InboundMessage], str | None],
        should_defer_turn: Callable[[InboundMessage, str, Iterable[str]], bool],
        missing_id_error: str,
        duplicate_id_error: Callable[[str], str],
        deferred_queues: dict[str, list[InboundMessage]] | None = None,
    ) -> None:
        self._publish_inbound = publish_inbound # 将消息发送到inbound的函数
        self._dispatch = dispatch
        self._is_running = is_running
        self._turn_id = turn_id # 获取turn_id的方法
        self._pending_id = pending_id
        self._should_defer_turn = should_defer_turn
        self._missing_id_error = missing_id_error
        self._duplicate_id_error = duplicate_id_error
        self.deferred_queues = deferred_queues if deferred_queues is not None else {} # 因为 session 当前正在运行，所以还没开始执行的 automation turns
        self._waiters: dict[str, asyncio.Future[OutboundMessage | None]] = {} # 已经提交、正在等待结果的 Automation Turn
        self._pending_messages_by_turn_id: dict[str, InboundMessage] = {} # 某个 session 当前有哪些 automation task 正在等待或者执行。

    # 提交一个自动turn，并等待它的响应
    async def submit(self, msg: InboundMessage) -> OutboundMessage | None:
        """Submit an automation turn and wait for its session response."""
        turn_id = self._turn_id(msg)
        if not turn_id:
            raise ValueError(self._missing_id_error)
        if turn_id in self._waiters:
            raise RuntimeError(self._duplicate_id_error(turn_id))

        # self._waiters等待future的结果
        loop = asyncio.get_running_loop()
        # 未来某个时刻会被别人填写结果的异步占位符
        # Future 可以被等待直到获得 result、exception 或被 cancelled；set_result() 和 set_exception() 会将其标记为完成。
        future: asyncio.Future[OutboundMessage | None] = loop.create_future()
        self._waiters[turn_id] = future
        self._pending_messages_by_turn_id[turn_id] = msg
        try:
            # 如果正在运行，发布到inbound里。否则直接dipatch处理，不用再传到总线
            if self._is_running():
                await self._publish_inbound(msg)
            else:
                await self._dispatch(msg)
            try:
                return await future # 等待结果
            # 除了取消error，其他统一向外暴露AutomationTurnError
            except asyncio.CancelledError:
                raise
            except AutomationTurnError:
                raise
            except Exception as exc:
                raise AutomationTurnError(str(exc) or exc.__class__.__name__) from exc
        finally:
            # 该消息已经处理完成，有结果了，将它从两个队列中删除
            self._waiters.pop(turn_id, None)
            self._pending_messages_by_turn_id.pop(turn_id, None)

    # 如果该session是active状态，推迟它
    def defer_if_active(
        self,
        msg: InboundMessage,
        *,
        session_key: str,
        active_session_keys: Iterable[str],
    ) -> bool:
        """Defer an automation turn when its target session is already active."""
        if not self._should_defer_turn(msg, session_key, active_session_keys):
            return False
        pending_msg = msg
        # 如果session_key和消息内的不同，用传入的值覆盖消息内的值
        if session_key != msg.session_key:
            pending_msg = dataclasses.replace(
                msg,
                session_key_override=session_key,
            )
        self.deferred_queues.setdefault(session_key, []).append(pending_msg)
        return True

    # 将结果（response或error）赋给future
    def complete(
        self,
        msg: InboundMessage,
        *,
        response: OutboundMessage | None = None,
        error: BaseException | None = None,
    ) -> None:
        turn_id = self._turn_id(msg)
        if not turn_id:
            return
        future = self._waiters.get(turn_id)
        if future is None or future.done():
            return
        if error is not None:
            if isinstance(error, asyncio.CancelledError):
                error = AutomationTurnError(str(error) or error.__class__.__name__)
            future.set_exception(error)
        else:
            future.set_result(response)

    # 返回某个sessoin的等待 自动 turn id（包括在deferred_queues延迟队列中的，还有_pending_messages_by_turn_id中的已提交等待结果的）
    def pending_ids_for_session(self, session_key: str) -> set[str]:
        """Return automation IDs that are waiting for or running in *session_key*."""
        pending_ids: set[str] = set()
        # 推迟队列中
        for msg in self.deferred_queues.get(session_key, []):
            pending_id = self._pending_id(msg)
            if pending_id:
                pending_ids.add(pending_id)
        # 等待结果队列中
        for msg in self._pending_messages_by_turn_id.values():
            if msg.session_key != session_key:
                continue
            pending_id = self._pending_id(msg)
            if pending_id:
                pending_ids.add(pending_id)
        return pending_ids
