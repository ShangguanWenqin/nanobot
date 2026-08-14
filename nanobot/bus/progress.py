"""Progress callback helpers for user-visible output.

These helpers convert agent progress callbacks into outbound chat messages.
Runtime state notifications such as turn lifecycle and model changes live in
``nanobot.bus.runtime_events``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from nanobot.bus.events import InboundMessage
from nanobot.bus.outbound_events import ProgressEvent, outbound_message_for_event
from nanobot.bus.queue import MessageBus


# 该适配器把 runner 的回调形态收束为总线事件，具体平台是否展示进度仍由 ChannelManager 决定。
def build_bus_progress_callback(
    bus: MessageBus,
    msg: InboundMessage,
) -> Callable[..., Awaitable[None]]:
    """Return a callback that publishes progress as outbound messages."""

    async def _publish_progress(
        content: str,
        *,
        tool_hint: bool = False,
        tool_events: list[dict[str, Any]] | None = None,
        file_edit_events: list[dict[str, Any]] | None = None,
        reasoning: bool = False,
        reasoning_end: bool = False,
    ) -> None:
        # 复制原入站路由信息，确保工具、推理与文件编辑进度回到触发本 turn 的会话边缘。
        await bus.publish_outbound(
            outbound_message_for_event(
                channel=msg.channel,
                chat_id=msg.chat_id,
                event=ProgressEvent(
                    content=content,
                    tool_hint=tool_hint,
                    reasoning_delta=reasoning,
                    reasoning_end=reasoning_end,
                    tool_events=tool_events,
                    file_edit_events=file_edit_events,
                ),
                metadata=msg.metadata,
            )
        )

    async def _bus_progress(
        content: str,
        *,
        tool_hint: bool = False,
        tool_events: list[dict[str, Any]] | None = None,
        file_edit_events: list[dict[str, Any]] | None = None,
        reasoning: bool = False,
        reasoning_end: bool = False,
    ) -> None:
        await _publish_progress(
            content,
            tool_hint=tool_hint,
            tool_events=tool_events,
            file_edit_events=file_edit_events,
            reasoning=reasoning,
            reasoning_end=reasoning_end,
        )

    return _bus_progress
