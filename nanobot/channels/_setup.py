"""Resolve channel-owned setup contracts for settings consumers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nanobot.channels.contracts import ChannelSetupSpec

if TYPE_CHECKING:
    from nanobot.channels.plugin import ChannelPlugin


# 管理端通过这一层取得声明式契约，避免为展示设置而加载带可选依赖的 channel runtime。
def channel_setup_spec(
    name: str,
    *,
    plugin: ChannelPlugin | None = None,
) -> ChannelSetupSpec | None:
    """Return the setup contract declared by one channel descriptor."""
    if plugin is None:
        from nanobot.channels.registry import load_channel_plugin

        plugin = load_channel_plugin(name)
    return plugin.setup
