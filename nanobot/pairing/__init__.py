"""Pairing module for DM sender approval."""

from nanobot.pairing.store import (
    approve_code,
    clear_channel,
    deny_code,
    format_expiry,
    format_pairing_reply,
    generate_code,
    get_approved,
    handle_pairing_command,
    is_approved,
    list_pending,
    revoke,
    revoke_channel,
)

# channel 和命令层用这两个 metadata 标记配对交互；它们只携带流程类型/代码，不改变入站授权的唯一判断位置。
# Metadata keys used by channels and commands to tag pairing-related messages.
PAIRING_CODE_META_KEY = "_pairing_code"
PAIRING_COMMAND_META_KEY = "_pairing_command"

__all__ = [
    "approve_code",
    "clear_channel",
    "deny_code",
    "format_expiry",
    "format_pairing_reply",
    "generate_code",
    "get_approved",
    "handle_pairing_command",
    "is_approved",
    "list_pending",
    "revoke",
    "revoke_channel",
    "PAIRING_CODE_META_KEY",
    "PAIRING_COMMAND_META_KEY",
]
