"""System-level notification for config file changes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from watchfiles import Change, awatch  # pyright: ignore[reportUnknownVariableType]


# watcher 只报告变更；配置重新加载和运行时热更新策略由 gateway 组合根决定。
async def watch_config_file(config_path: Path, on_change: Callable[[], None]) -> None:
    """Notify ``on_change`` after the configured file changes."""
    target = config_path.resolve(strict=False)

    def is_config_file(_change: Change, changed_path: str) -> bool:
        return Path(changed_path).resolve(strict=False) == target

    async for _changes in awatch(
        target.parent,
        watch_filter=is_config_file,
        recursive=False,
    ):
        on_change()
