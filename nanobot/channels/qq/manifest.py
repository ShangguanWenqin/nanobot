"""QQ management contract."""

from nanobot.channels._manifest import field, required_fields
from nanobot.channels.contracts import ChannelSetupSpec
from nanobot.channels.plugin import ChannelPlugin

# QQ 的 markdown/plain 输出格式是平台展示能力，不改变总线中的统一文本消息契约。
SETUP_SPEC = ChannelSetupSpec(
    fields={
        "appId": field(),
        "secret": field("secret"),
        "allowFrom": field("list"),
        "msgFormat": field("enum", choices={"plain", "markdown"}, default="plain"),
    },
    required=required_fields("appId", "secret"),
    official_url="https://q.qq.com/",
)

PLUGIN = ChannelPlugin(
    name="qq",
    display_name="QQ",
    runtime=f"{__package__}.runtime:QQChannel",
    setup=SETUP_SPEC,
    dependencies=(
        "aiohttp>=3.9.0,<4.0.0",
        "qq-botpy>=1.2.0,<2.0.0",
    ),
    webui="webui/index.ts",
)
