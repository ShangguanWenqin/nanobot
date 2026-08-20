"""WeCom management contract."""

from nanobot.channels._manifest import field, required_fields
from nanobot.channels.contracts import ChannelSetupSpec
from nanobot.channels.plugin import ChannelPlugin

# WeCom AI Bot 通过 botId/secret 建立 SDK 会话；媒体大小与帧事件限制留给 runtime 解释。
SETUP_SPEC = ChannelSetupSpec(
    fields={
        "botId": field(),
        "secret": field("secret"),
        "allowFrom": field("list"),
    },
    required=required_fields("botId", "secret"),
    official_url="https://developer.work.weixin.qq.com/",
)

PLUGIN = ChannelPlugin(
    name="wecom",
    display_name="WeCom",
    runtime=f"{__package__}.runtime:WecomChannel",
    setup=SETUP_SPEC,
    dependencies=("wecom-aibot-sdk-python>=0.1.5",),
    webui="webui/index.ts",
)
