"""Runtime log visibility controls shared by CLI commands."""

from loguru import logger

__all__ = ["_set_nanobot_logs"]


# CLI 仅切换 nanobot 命名空间的可见性，避免改写宿主进程其他库的日志策略。
def _set_nanobot_logs(enabled: bool) -> None:
    if enabled:
        logger.enable("nanobot")
    else:
        logger.disable("nanobot")
