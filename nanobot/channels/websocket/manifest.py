"""WebSocket management contract."""

from nanobot.channels.contracts import ChannelSetupSpec
from nanobot.channels.plugin import ChannelPlugin
from nanobot.channels.websocket.validation import validate

# WebSocket 是 gateway 自带的浏览器入口，always_enabled 表示设置界面不能把它当作普通可停用插件。
SETUP_SPEC = ChannelSetupSpec(
    fields={},
    official_url="http://127.0.0.1:8765",
    validator=validate,
)

PLUGIN = ChannelPlugin(
    name="websocket",
    display_name="WebSocket",
    runtime=f"{__package__}.runtime:WebSocketChannel",
    setup=SETUP_SPEC,
    default_enabled=True,
    capabilities=frozenset({"always_enabled"}),
    webui="webui/index.ts",
)
