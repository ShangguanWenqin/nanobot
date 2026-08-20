"""Signal management contract."""

from nanobot.channels._manifest import field, required
from nanobot.channels.contracts import ChannelSetupSpec
from nanobot.channels.plugin import ChannelPlugin

# Signal 通过 signal-cli REST daemon 工作，私聊和群组 allowlist 分开以匹配其不同的地址空间。
SETUP_SPEC = ChannelSetupSpec(
    fields={
        "phoneNumber": field(),
        "daemonHost": field(default="localhost"),
        "daemonPort": field("int", default=8080),
        "dm.allowFrom": field("list"),
        "group.allowFrom": field("list"),
    },
    required=(required("phoneNumber"),),
    official_url="https://github.com/bbernhard/signal-cli-rest-api",
)

PLUGIN = ChannelPlugin(
    name="signal",
    display_name="Signal",
    runtime=f"{__package__}.runtime:SignalChannel",
    setup=SETUP_SPEC,
    webui="webui/index.ts",
)
