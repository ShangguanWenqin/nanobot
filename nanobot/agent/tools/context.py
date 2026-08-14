"""Runtime context for tool construction.
ToolContext：工具“被创建时”的上下文。
RequestContext：工具“被执行时”的请求上下文。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager
    from nanobot.agent.tools.exec_session import ExecSessionManager
    from nanobot.agent.tools.file_state import FileStates
    from nanobot.bus.queue import MessageBus
    from nanobot.bus.runtime_events import RuntimeEventBus
    from nanobot.config.schema import ProviderConfig, ToolsConfig
    from nanobot.cron.service import CronService
    from nanobot.providers.factory import ProviderSnapshot
    from nanobot.security.workspace_access import WorkspaceSandboxStatus
    from nanobot.session.manager import SessionManager
    from nanobot.utils.llm_runtime import LLMRuntime

_CURRENT_REQUEST_CONTEXT: ContextVar["RequestContext | None"] = ContextVar(
    "nanobot_tool_request_context",
    default=None,
)


# 现在正在执行的这一轮请求是谁发来的
@dataclass(frozen=True) # 请求快照，生成后就不能修改
class RequestContext:
    """Per-request context injected into tools at message-processing time."""
    channel: str
    chat_id: str
    message_id: str | None = None
    session_key: str | None = None
    original_user_text: str | None = None
    runtime: LLMRuntime | None = None
    metadata: dict[str, Any] = field(default_factory=dict) # metadata这个属性不能被重新赋值，但它本身这个dict还是可以修改
    sender_id: str | None = None
    turn_id: str | None = None
    workspace: Path | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


# 如果某个Tool 具有 set_context()，那么它可以被认为是 ContextAware，不太理解这里的意思 register.py这一行就用到了这个。
# if isinstance(tool, ContextAware) and (ctx := current_request_context()) is not None: 
# 原来的Tool 自己保存RequestContext，新架构从ContextVar获取
@runtime_checkable
class ContextAware(Protocol):
    def set_context(self, ctx: RequestContext) -> None:
        ...


# 绑定请求上下文
def bind_request_context(ctx: RequestContext) -> Token[RequestContext | None]:
    return _CURRENT_REQUEST_CONTEXT.set(ctx)


# 重置请求上下文
def reset_request_context(token: Token[RequestContext | None]) -> None:
    _CURRENT_REQUEST_CONTEXT.reset(token)


# 绑定一个不可变的请求快照并恢复之前的值（请求->执行->恢复）
# @contextmanager装饰器表示它是给with用的 with结束后，执行finally 语句
@contextmanager
def request_context(ctx: RequestContext):
    """Bind one immutable request snapshot and restore the previous value."""
    token = bind_request_context(ctx)
    try:
        yield ctx
    finally:
        reset_request_context(token)


# 获取当前的请求上下文
def current_request_context() -> RequestContext | None:
    return _CURRENT_REQUEST_CONTEXT.get()


# 获取当前请求上下文的sesson_key
def current_request_session_key() -> str | None:
    ctx = current_request_context()
    return ctx.session_key if ctx else None

# 我要创建一个 Tool，需要给它哪些运行环境
@dataclass
class ToolContext:
    config: ToolsConfig
    workspace: str
    bus: MessageBus | None = None
    subagent_manager: SubagentManager | None = None
    cron_service: CronService | None = None
    exec_session_manager: ExecSessionManager | None = None
    sessions: SessionManager | None = None
    file_state_store: FileStates | None = None
    provider_snapshot_loader: Callable[..., ProviderSnapshot] | None = None
    image_generation_provider_configs: dict[str, ProviderConfig] | None = None
    timezone: str = "UTC"
    workspace_sandbox: WorkspaceSandboxStatus | None = None
    runtime_events: RuntimeEventBus | None = None
