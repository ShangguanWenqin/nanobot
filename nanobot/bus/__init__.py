"""Message bus module for decoupled channel-agent communication."""

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
# __init__.py 告诉python该目录是个模块，另外__all__表明该包对外暴露的类是哪些
__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
