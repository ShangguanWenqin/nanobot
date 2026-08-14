"""Shared recovery guidance for OAuth dependency failures."""

# 多个 OAuth 后端共享同一依赖缺失提示，避免各登录入口暴露不一致的安装建议。
OAUTH_CLI_KIT_MISSING_MESSAGE = (
    "This nanobot installation is missing the required oauth-cli-kit package. "
    "Reinstall or upgrade nanobot-ai using the same installation method."
)
