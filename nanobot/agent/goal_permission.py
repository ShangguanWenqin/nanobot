"""Turn-local permission for explicit sustained-goal mutations."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_GOAL_MUTATION_ALLOWED: ContextVar[bool] = ContextVar(
    "nanobot_goal_mutation_allowed",
    default=False,
)


# 持续目标修改能力按当前异步调用链绑定，不能从某个获准 turn 泄漏到后续请求。
def goal_mutation_allowed() -> bool:
    return _GOAL_MUTATION_ALLOWED.get()


def revoke_goal_mutation_permission() -> None:
    _GOAL_MUTATION_ALLOWED.set(False)


@contextmanager
def goal_mutation_permission(allowed: bool):
    """Bind goal permission for one agent-run or direct tool execution scope."""
    # 保存 token 而非退出时硬编码为 False，才能正确恢复外层嵌套作用域的原值。
    token = _GOAL_MUTATION_ALLOWED.set(allowed)
    try:
        yield
    finally:
        _GOAL_MUTATION_ALLOWED.reset(token)
