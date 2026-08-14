"""Tool discovery and registration via package scanning.

解决 “系统启动时，我有哪些 Tool？哪些 Tool 应该加载？怎么创建它们？”
              Tool 的定义
                 │
                 ▼
             Tool Class
                 │
        ┌────────┴────────┐
        │                 │
        │ ToolLoader       │
        │ 发现 + 筛选      │
        │ 实例化           │
        └────────┬────────┘
                 │
                 ▼
             Tool Instance
                 │
                 │ register()
                 ▼
           ToolRegistry
                 │
       ┌─────────┴──────────┐
       │                    │
 get_definitions()      execute()
       │                    │
       ▼                    ▼
    LLM Tool Schema     Tool.execute()"""
from __future__ import annotations

import importlib
import pkgutil
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.agent.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from nanobot.agent.tools.context import RequestContext, ToolContext

# 过滤掉tools/ 下的非工具文件
_SKIP_MODULES = frozenset({
    "base", "schema", "registry", "context", "loader", "config",
    "file_state", "sandbox", "mcp", "__init__", "runtime_control",
})


class ToolLoader:
    def __init__(self, package: Any = None, *, test_classes: list[type[Tool]] | None = None):
        if package is None:
            import nanobot.agent.tools as _pkg
            package = _pkg
        self._package = package
        self._test_classes = test_classes
        self._discovered: list[type[Tool]] | None = None
        self._plugins: dict[str, type[Tool]] | None = None

    # nanobot.agent.tools 包下面的 Python 模块，找到所有符合条件的 Tool 子类
    # 注意这里返回的是type[Tool]，也就是说它返回的是Tool类或其子类的list，而不是Tool类实例的list。（Tool是抽象类，不会有实例）
    def discover(self) -> list[type[Tool]]: # 这是什么意思？type[tool]
        # 已经有了直接返回
        if self._test_classes is not None:
            return list(self._test_classes)
        if self._discovered is not None:
            return self._discovered
        seen: set[int] = set()
        results: list[type[Tool]] = []
        for _importer, module_name, _ispkg in pkgutil.iter_modules(self._package.__path__):
            if module_name.startswith("_") or module_name in _SKIP_MODULES:
                continue
            try:
                module = importlib.import_module(f".{module_name}", self._package.__name__)
            except Exception:
                logger.exception("Failed to import tool module: %s", module_name)
                continue
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type) # 必须是class
                    and issubclass(attr, Tool) # 必须继承Tool 类
                    and attr is not Tool # 不能是抽象基类Tool 本身
                    and not attr_name.startswith("_") # 不能是下划线开头
                    and not getattr(attr, "__abstractmethods__", None) # 非抽象类
                    and getattr(attr, "_plugin_discoverable", True) # 可被插件发现
                    and id(attr) not in seen # 避免重复
                ):
                    seen.add(id(attr))
                    results.append(attr)
        results.sort(key=lambda cls: cls.__name__)
        self._discovered = results # 缓存
        return results

    # 发现外部工具插件
    def _discover_plugins(self) -> dict[str, type[Tool]]:
        """Discover external tool plugins registered via entry_points."""
        if self._plugins is not None:
            return self._plugins
        plugins: dict[str, type[Tool]] = {}
        try:
            # 查找所有声明自己属于 nanobot.tools 这个 Entry Point Group 的插件。
            eps = entry_points(group="nanobot.tools")
        except Exception:
            return plugins
        for ep in eps:
            try:
                cls = ep.load()
                if (
                    isinstance(cls, type) # 必须是class
                    and issubclass(cls, Tool) # 必须是tool 的子类
                    and not getattr(cls, "__abstractmethods__", None) # 非抽象类
                    and getattr(cls, "_plugin_discoverable", True) # 可被插件发现
                ):
                    plugins[ep.name] = cls
            except Exception:
                logger.exception("Failed to load tool plugin: %s", ep.name)
        self._plugins = plugins
        return plugins

    # 把符合条件的 Tool 真正变成实例，并注册到 Registry
    def load(self, ctx: ToolContext, registry: ToolRegistry, *, scope: str = "core") -> list[str]:
        registered: list[str] = []
        builtin_names: set[str] = set()
        sources = [(self.discover(), False), (self._discover_plugins().values(), True)]
        for source, is_plugin_source in sources:
            for tool_cls in source:
                cls_label = tool_cls.__name__
                try:
                    if scope not in getattr(tool_cls, "_scopes", {"core"}): # 只加载特定的scope场景下的tool
                        continue
                    if not tool_cls.enabled(ctx): # 该tool 是否启用
                        continue
                    tool = tool_cls.create(ctx) # 创建该工具类
                    if is_plugin_source:
                        tool = _LegacyErrorPrefixTool(tool) # 外部工具做兼容
                    if registry.has(tool.name):
                        # 外部工具和内置工具冲突
                        if is_plugin_source and tool.name in builtin_names:
                            logger.warning(
                                "Plugin %s skipped: conflicts with built-in tool %s",
                                cls_label, tool.name,
                            )
                            continue
                        logger.warning(
                            "Tool name collision: %s from %s overwrites existing",
                            tool.name, cls_label,
                        )
                    registry.register(tool)
                    registered.append(tool.name)
                    if not is_plugin_source:
                        builtin_names.add(tool.name)
                except Exception:
                    logger.exception("Failed to register tool: %s", cls_label)
        return registered


# 兼容性适配器（只在执行的时候把错误输出包装成ToolResult）
class _LegacyErrorPrefixTool(Tool):
    """Compatibility wrapper for external tools using the old error-string contract."""

    _plugin_discoverable = False

    def __init__(self, wrapped: Tool) -> None:
        self._wrapped = wrapped

    @property
    def name(self) -> str:
        return self._wrapped.name

    @property
    def description(self) -> str:
        return self._wrapped.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._wrapped.parameters

    def runtime_context_provider(self):
        return self._wrapped.runtime_context_provider()

    @property
    def read_only(self) -> bool:
        return self._wrapped.read_only

    @property
    def exclusive(self) -> bool:
        return self._wrapped.exclusive

    @property
    def concurrency_safe(self) -> bool:
        return self._wrapped.concurrency_safe

    @property
    def config_key(self) -> str:
        return getattr(self._wrapped, "config_key", "")

    def set_context(self, ctx: RequestContext) -> None:
        set_context = getattr(self._wrapped, "set_context", None)
        if callable(set_context):
            set_context(ctx)

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._wrapped.cast_params(params)

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        return self._wrapped.validate_params(params)

    def to_schema(self) -> dict[str, Any]:
        return self._wrapped.to_schema()

    # 包装error result
    async def execute(self, **kwargs: Any) -> Any:
        result = await self._wrapped.execute(**kwargs)
        if (
            isinstance(result, str)
            and not isinstance(result, ToolResult)
            and result.startswith("Error:")
        ):
            return ToolResult.error(result)
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


    """
    ┌─────────────────────────────────────────────┐
│                  base.py                    │
│                                             │
│  定义 Tool 抽象能力                         │
│  定义 Schema / 参数验证                     │
│  定义 ToolResult / 错误语义                 │
│  定义 Tool 元数据                           │
└────────────────────┬────────────────────────┘
                     │
                     │ Tool Class
                     ▼
┌─────────────────────────────────────────────┐
│                 loader.py                   │
│                                             │
│  找到 Tool Class                             │
│  判断 scope                                  │
│  判断 enabled                                │
│  create(ctx) 实例化                          │
│  加载第三方 Plugin                           │
│  兼容旧插件                                  │
└────────────────────┬────────────────────────┘
                     │
                     │ Tool Instance
                     ▼
┌─────────────────────────────────────────────┐
│                registry.py                  │
│                                             │
│  register / unregister                       │
│  name lookup                                 │
│  Tool Schema                                 │
│  参数 cast                                   │
│  参数 validate                               │
│  execute                                     │
│  Runtime Context Provider                    │
└─────────────────────────────────────────────┘
"""
