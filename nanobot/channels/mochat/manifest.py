"""MoChat management contract."""

from nanobot.channels._manifest import field, required
from nanobot.channels.contracts import ChannelSetupSpec
from nanobot.channels.plugin import ChannelPlugin

# MoChat 使用 socket.io/msgpack，且 settings_visible=False，声明仍供运行时依赖检查和发现使用。
SETUP_SPEC = ChannelSetupSpec(
    fields={
        "baseUrl": field(default="https://mochat.io"),
        "clawToken": field("secret"),
        "agentUserId": field(),
        "sessions": field("list"),
        "panels": field("list"),
        "allowFrom": field("list"),
    },
    required=(required("clawToken"),),
    official_url="https://mochat.io/",
)

PLUGIN = ChannelPlugin(
    name="mochat",
    display_name="MoChat",
    runtime=f"{__package__}.runtime:MochatChannel",
    setup=SETUP_SPEC,
    dependencies=(
        "python-socketio>=5.16.0,<6.0.0",
        "msgpack>=1.1.0,<2.0.0",
    ),
    settings_visible=False,
)
