"""Helpers for routing bound cron turns back through their origin session."""

from __future__ import annotations

from typing import Any

from nanobot.cron.types import CronJob


def origin_delivery_context(job: CronJob) -> tuple[str, str, dict[str, Any]]:
    """Return ``(channel, chat_id, metadata)`` for a session-bound cron job."""
    # bound cron 的目标会话与回送地址一起固化在 payload，避免跨 channel 误投递。
    payload = job.payload
    if not payload.origin_channel or not payload.origin_chat_id:
        raise ValueError(f"cron job {job.id} is missing origin delivery context")
    return payload.origin_channel, payload.origin_chat_id, dict(payload.origin_metadata or {})
