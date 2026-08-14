"""Turn-scoped hook assembly for agent runs.
turn_hooks.py 不负责“实现 Hook”，它负责“为一次 Agent Turn 组装 Hook 链”。

如果把整个 Agent Runner 想象成一个发动机：

* AgentHook：定义“发动机在哪些位置可以插入观察/处理逻辑”
* AgentProgressHook：一个具体插件，把 Runner 的事件转换成用户能看到的进度、流式输出
* 其他 AgentHook：日志、Tracing、统计、插件扩展等
* turn_hooks.py：负责决定这一轮到底装哪些 Hook，以及装的顺序
* CompositeHook：把多个 Hook 串成一个整体交给 Runner
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.hook import (
    AgentHook,
    AgentTurnHookContext,
    AgentTurnHookFactory,
    CompositeHook,
)
from nanobot.agent.progress_hook import AgentProgressHook


# 构建这一轮 Hook 链所需要的全部输入参数
@dataclass(slots=True)
class AgentTurnHookSpec:
    """Inputs needed to build the hook chain for one agent turn."""

    # 用户交互类
    on_progress: Callable[..., Awaitable[None]] | None = None
    on_stream: Callable[[str], Awaitable[None]] | None = None
    on_stream_end: Callable[..., Awaitable[None]] | None = None
    # Turn 的上下文信息
    channel: str = "cli"
    chat_id: str = "direct"
    message_id: str | None = None
    metadata: dict[str, Any] | None = None
    session_key: str | None = None
    workspace: Path | None = None

    tool_hint_max_length: int = 40
    on_iteration: Callable[[int], None] | None = None
    # hook 创建工厂，每次个turn返回的都是独立的hook实体 AgentTurnHookFactory = Callable[[AgentTurnHookContext], AgentHook | None]
    registered_hook_factories: list[AgentTurnHookFactory] = field(default_factory=list)
    turn_hook_factories: list[AgentTurnHookFactory] = field(default_factory=list)
    # 注册hook和turn 的hook，注册的生命周期比turn要长
    registered_hooks: list[AgentHook] = field(default_factory=list)
    turn_hooks: list[AgentHook] = field(default_factory=list)
    # 是否为临时turn
    ephemeral: bool = False
    # 是否为临时turn后见额外的hook
    run_extra_hooks_for_ephemeral: bool = False
    attributes: dict[str, Any] | None = None

# 1. 创建 ProgressHook
# 2. 创建其他 Hook
# 3. 按固定顺序组成 CompositeHook
def build_agent_turn_hook(spec: AgentTurnHookSpec) -> AgentHook:
    """Build the hook chain used by ``AgentRunner`` for one turn."""
    # 创建 ProgressHook
    progress_hook = AgentProgressHook(
        on_progress=spec.on_progress,
        on_stream=spec.on_stream,
        on_stream_end=spec.on_stream_end,
        session_key=spec.session_key,
        tool_hint_max_length=spec.tool_hint_max_length,
        on_iteration=spec.on_iteration,
    )
    # 如果是临时turn，并且没有为临时turn构建特殊的hook，只用进度hook
    if spec.ephemeral and not spec.run_extra_hooks_for_ephemeral:
        return progress_hook

    turn_context = AgentTurnHookContext(
        on_progress=spec.on_progress,
        workspace=spec.workspace,
        channel=spec.channel,
        chat_id=spec.chat_id,
        message_id=spec.message_id,
        session_key=spec.session_key,
        metadata=dict(spec.metadata or {}),
        attributes=dict(spec.attributes or {}),
        ephemeral=spec.ephemeral,
    )
    hook_chain: list[AgentHook] = [progress_hook]

    # 构建其它注册hook
    for factory in spec.registered_hook_factories:
        try:
            created_hook = factory(turn_context)
        except Exception:
            logger.exception("Agent turn hook factory failed: {}", factory)
            continue
        if created_hook is not None:
            hook_chain.append(created_hook)

    # 加入已经有的注册hook
    hook_chain.extend(spec.registered_hooks)

    # 构建turn hook
    for factory in spec.turn_hook_factories:
        try:
            created_hook = factory(turn_context)
        except Exception:
            logger.exception("Agent turn hook factory failed: {}", factory)
            continue
        if created_hook is not None:
            hook_chain.append(created_hook)
    # 加入已有的turn hook
    hook_chain.extend(spec.turn_hooks)
    # 返回CompositeHook（如果不只一个）
    return CompositeHook(hook_chain) if len(hook_chain) > 1 else progress_hook
