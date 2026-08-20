"""Visibility helpers for persisted session history messages."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nanobot.session.automation_turns import is_automation_history_message

HIDDEN_HISTORY_META = "_hidden_history"


def _has_hidden_history_marker(message: Mapping[str, Any] | None) -> bool:
    if not message:
        return False
    marker = message.get(HIDDEN_HISTORY_META)
    return marker is True or isinstance(marker, Mapping)


def is_hidden_history_message(message: Mapping[str, Any] | None) -> bool:
    # 持久化不等于公开展示：自动化/内部记录可供审计，却不能伪装为聊天 turn。
    """True for persisted messages that should not be shown as chat turns."""
    return _has_hidden_history_marker(message) or is_automation_history_message(message)
