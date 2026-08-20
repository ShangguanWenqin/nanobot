"""DingTalk management contract."""

from nanobot.channels._manifest import field, required_fields
from nanobot.channels.contracts import ChannelSetupSpec
from nanobot.channels.plugin import ChannelPlugin

# 钉钉使用 client 凭据和流式 SDK；此声明让设置页能先校验字段，再由 manager 按需装载依赖。
SETUP_SPEC = ChannelSetupSpec(
    fields={
        "clientId": field(),
        "clientSecret": field("secret"),
        "allowFrom": field("list"),
    },
    required=required_fields("clientId", "clientSecret"),
    official_url="https://open.dingtalk.com/",
)

PLUGIN = ChannelPlugin(
    name="dingtalk",
    display_name="DingTalk",
    runtime=f"{__package__}.runtime:DingTalkChannel",
    setup=SETUP_SPEC,
    dependencies=("dingtalk-stream>=0.24.0,<1.0.0",),
    webui="webui/index.ts",
)
