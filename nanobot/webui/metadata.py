"""Shared WebUI metadata keys."""

# 这些键写入 session/transcript 时是内部协议标记，不能把浏览器任意 metadata 当成同等可信的运行时状态。
WEBUI_TURN_METADATA_KEY = "webui_turn_id"
WEBUI_SYSTEM_COMMAND_TURN_PREFIX = "webui-system:"
WEBSOCKET_TURN_OWNER_METADATA_KEY = "_websocket_turn_owner"
WEBUI_MESSAGE_SOURCE_METADATA_KEY = "_webui_message_source"
