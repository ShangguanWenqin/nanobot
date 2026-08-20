"""WeChat setup validation owned by the channel package."""

from typing import Any

from nanobot.channels.contracts import ChannelValidationContext
from nanobot.channels.validation import check, enabled, official_action, payload, string_value


# 微信凭据由终端二维码流程生成；检查本地状态后只提示下一步，而不在浏览器发起登录网络请求。
def validate(values: dict[str, Any], _context: ChannelValidationContext) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    if enabled(values) or string_value(values.get("token")):
        checks.append(
            check("local_state", "Local login state", "pass", "Saved local login state was detected.")
        )
        return payload("weixin", "configured", checks, can_enable=True)
    checks.append(
        check(
            "terminal_login",
            "Terminal login",
            "skipped",
            "This channel uses a terminal QR login flow.",
            action_url=official_action("weixin"),
        )
    )
    return payload(
        "weixin",
        "needs_setup",
        checks,
        missing_fields=["terminal_login"],
        can_enable=False,
    )


__all__ = ["validate"]
