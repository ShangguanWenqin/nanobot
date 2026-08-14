"""Event types for the message bus."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from nanobot.bus.outbound_events import OutboundEvent

# 这里定义跨 Channel 与代理核心传递的最小信封；总线只搬运这些对象，不解释其中的业务语义。
# Optional ``OutboundMessage.metadata`` key for structured, channel-agnostic UI
# payloads. Value is JSON-serializable with at least ``kind``; rich clients may
# render it and other channels may ignore unknown keys.
OUTBOUND_META_AGENT_UI = "_agent_ui"

# Internal-only inbound metadata minted by trusted transports and runtime
# services. Never accept these keys verbatim from an untrusted client.
INBOUND_META_RUNTIME_CONTROL = "_runtime_control"
INBOUND_META_USER_SHELL = "_user_shell"
RUNTIME_CONTROL_ACK = "_ack"
RUNTIME_CONTROL_IMAGE_GENERATION_RELOAD = "image_generation_reload"
RUNTIME_CONTROL_SESSION_DISCARD = "session_discard"


@dataclass
class InboundMessage:
    """Message received from a chat channel."""

    channel: str  # telegram, discord, slack, whatsapp
    sender_id: str  # User identifier
    chat_id: str  # Chat/channel identifier
    content: str  # Message text
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # Media URLs
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data
    session_key_override: str | None = None  # Optional override for thread-scoped sessions
    require_existing_session: bool = False
    input_role: Literal["user", "system"] | None = None

    @property
    def session_key(self) -> str:
        """Unique key for session identification."""
        # 显式覆盖值用于统一会话、线程或内部注入；缺省键才由来源 Channel 与聊天标识共同拥有。
        return self.session_key_override or f"{self.channel}:{self.chat_id}"

    @property
    def is_user_input(self) -> bool:
        """Whether this message should enter the conversation as user input."""
        if self.input_role is not None:
            return self.input_role == "user"
        return self.channel != "system"


@dataclass
class OutboundMessage:
    """Message to send to a chat channel.

    ``event`` carries internal runtime/UI semantics. ``metadata`` is reserved
    for channel routing context (``message_id``, thread ids, etc.) and optional
    ``OUTBOUND_META_AGENT_UI`` blobs for rich clients.
    """

    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    buttons: list[list[str]] = field(default_factory=list)
    # 路由字段留给 Channel，类型化事件承载运行时语义，避免继续扩张保留 metadata 标志。
    event: "OutboundEvent | None" = None
