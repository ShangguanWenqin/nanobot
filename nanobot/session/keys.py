"""Shared session key constants and helpers."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

UNIFIED_SESSION_KEY = "unified:default"
# 统一会话仍用明确保留 key，避免将不同来源的 route 信息当成会话身份的一部分。
LAST_CHANNEL_METADATA_KEY = "last_channel"


def session_key_for_channel(channel: str, chat_id: str, *, unified_session: bool = False) -> str:
    """Return the session key for a channel/chat pair."""
    if unified_session:
        return UNIFIED_SESSION_KEY
    return f"{channel}:{chat_id}"


def remember_last_channel(
    metadata: MutableMapping[str, Any],
    channel: str,
    chat_id: str,
) -> None:
    """Persist the latest concrete delivery route in session metadata."""
    if not channel or not chat_id:
        return
    # 路由是会话 metadata 的持久提示；自动化回送还须校验自己的 origin 绑定。
    metadata[LAST_CHANNEL_METADATA_KEY] = f"{channel}:{chat_id}"


def last_channel_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> tuple[str, str] | None:
    """Return a concrete delivery route from persisted session metadata."""
    if not isinstance(metadata, Mapping):
        return None
    route = metadata.get(LAST_CHANNEL_METADATA_KEY)
    if not isinstance(route, str) or ":" not in route:
        return None
    channel, chat_id = route.split(":", 1)
    if not channel or not chat_id:
        return None
    return channel, chat_id
